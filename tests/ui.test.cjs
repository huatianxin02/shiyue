/* DOM-level checks for the two controls the user asked for: disconnecting a source
   and deleting a background task.

   jsdom 26 ships no <dialog> implementation, so this harness polyfills the three
   methods the app calls. Everything else runs against the real web/app.js, which
   keeps the test honest about event delegation and rendering. */
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8').replace('__VAULT_TOKEN__', 'test-token');
const app = fs.readFileSync(path.join(root, 'web/app.js'), 'utf8')+'\n'+fs.readFileSync(path.join(root,'web/features.js'),'utf8')+'\n'+fs.readFileSync(path.join(root,'web/discovery.js'),'utf8')+'\n'+fs.readFileSync(path.join(root,'web/organization.js'),'utf8');

const stamp = '2026-10-18T10:00:00+00:00';
const source = (id, name, count) => ({ id, name, root: `/Users/someone/${name}`, mode: 'managed', kind: 'folder', created_at: stamp, last_scan: stamp, count, online: true });

const collection = (id, name, count = 0) => ({ id, name, count, created_at: stamp });

function status(sources, jobs, collections = []) {
  return {
    version: '0.3.0', paused: false, data_path: '/tmp/vault', cameras: [], months: [],
    stats: { files: sources.reduce((n, s) => n + s.count, 0), bytes: 2048, managed_bytes: 2048, objects: 2, starred: 0, warnings: 0, counts: { document: 2 } },
    sources, collections, jobs
  };
}

function dragEvent(app,element,type,transfer,clientY=120){
  const event=new app.window.MouseEvent(type,{bubbles:true,cancelable:true,clientY});
  Object.defineProperty(event,'dataTransfer',{value:transfer});
  element.dispatchEvent(event);
  return event;
}
function collectionTransfer(){
  const data={};
  return {setData:(type,value)=>data[type]=value,getData:type=>data[type]||'',get types(){return Object.keys(data);}};
}
const dragCollections=()=>[
  {...collection('root','经学',1),depth:1,parent_id:null,child_count:1,path:['经学']},
  {...collection('child','易学',1),depth:2,parent_id:'root',child_count:0,path:['经学','易学']},
  {...collection('other','书籍',0),depth:1,parent_id:null,child_count:0,path:['书籍']}
];

test('分类拖动边缘排序、中间移入，轮询期间保留拖动节点',async()=>{
  for(const [y,position] of [[101,'before'],[120,'inside'],[139,'after']]){
    const app=await boot({collections:dragCollections()});
    try{
      const source=app.doc.querySelector('[data-collection="other"]'),target=app.doc.querySelector('[data-collection="root"]');
      target.getBoundingClientRect=()=>({top:100,height:40});
      const transfer=collectionTransfer();
      dragEvent(app,source,'dragstart',transfer);
      app.doc.querySelector('#refresh-button').click();await app.flush();
      assert.equal(app.doc.querySelector('[data-collection="other"]'),source);
      assert.equal(app.doc.querySelector('#collection-root-drop').hidden,false);
      assert.equal(dragEvent(app,target,position==='before'?'dragenter':'dragover',transfer,y).defaultPrevented,true);
      assert.ok(target.classList.contains('collection-drop-'+position));
      dragEvent(app,target,'drop',transfer,y);await app.flush();
      assert.deepEqual(app.calls.find(c=>c.route.includes('/collection/move')).body,{collection_id:'other',target_id:'root',position});
      assert.equal(app.doc.querySelector('#collection-root-drop').hidden,true);
      assert.equal(app.doc.querySelector('#drop-overlay').hidden,true);
      assert.equal(app.doc.querySelector('#import-dialog').open,false);
    }finally{app.close();}
  }
});

test('子分类拖回顶级，键盘按同级顺序移动',async()=>{
  const app=await boot({collections:dragCollections()});
  try{
    const transfer=collectionTransfer();
    dragEvent(app,app.doc.querySelector('[data-collection="child"]'),'dragstart',transfer);
    const target=app.doc.querySelector('#collection-root-drop');
    dragEvent(app,target,'dragover',transfer);dragEvent(app,target,'drop',transfer);await app.flush();
    assert.deepEqual(app.calls.find(c=>c.route.includes('/collection/move')).body,{collection_id:'child',parent_id:null});
    const other=app.doc.querySelector('[data-collection="other"]');
    other.dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'ArrowUp',altKey:true,bubbles:true,cancelable:true}));
    await app.flush();
    assert.deepEqual(app.calls.filter(c=>c.route.includes('/collection/move')).at(-1).body,{collection_id:'other',target_id:'root',position:'before'});
  }finally{app.close();}
});

test('分类拒绝循环、超过九级与同级重名，取消拖动不写入',async()=>{
  const cols=dragCollections();
  cols.push({...collection('deep','深层'),depth:9,parent_id:'root',child_count:0});
  cols.push({...collection('duplicate','书籍'),depth:2,parent_id:'root',child_count:0});
  const app=await boot({collections:cols});
  try{
    for(const [from,to] of [['root','child'],['other','deep'],['other','root']]){
      const transfer=collectionTransfer(),target=app.doc.querySelector(`[data-collection="${to}"]`);
      target.getBoundingClientRect=()=>({top:100,height:40});
      dragEvent(app,app.doc.querySelector(`[data-collection="${from}"]`),'dragstart',transfer);
      dragEvent(app,target,'dragover',transfer);assert.ok(target.classList.contains('collection-drop-invalid'));
      dragEvent(app,target,'drop',transfer);await app.flush();
    }
    const transfer=collectionTransfer();
    dragEvent(app,app.doc.querySelector('[data-collection="other"]'),'dragstart',transfer);
    app.doc.dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
    assert.equal(app.doc.querySelector('#collection-root-drop').hidden,true);
    assert.equal(app.calls.filter(c=>c.route.includes('/collection/move')).length,0);
  }finally{app.close();}
});

test('分类拖动失败保留原树，外部伪造拖动不能修改分类',async()=>{
  const app=await boot({collections:dragCollections(),responder:route=>{if(route==='/api/collection/move')throw Error('服务暂不可用');}});
  try{
    const target=app.doc.querySelector('[data-collection="root"]');
    target.getBoundingClientRect=()=>({top:100,height:40});
    const transfer=collectionTransfer();transfer.setData('application/x-shiyue-collection','other');
    dragEvent(app,target,'dragover',transfer);dragEvent(app,target,'drop',transfer);await app.flush();
    assert.equal(app.calls.filter(c=>c.route.includes('/collection/move')).length,0);
    dragEvent(app,app.doc.querySelector('[data-collection="other"]'),'dragstart',transfer);
    dragEvent(app,target,'dragover',transfer);dragEvent(app,target,'drop',transfer);await app.flush();
    assert.deepEqual([...app.doc.querySelectorAll('[data-collection]')].map(e=>e.dataset.collection),['root','child','other']);
    assert.equal(app.doc.querySelector('#toast').textContent,'服务暂不可用');
    assert.equal(app.doc.querySelector('#collection-root-drop').hidden,true);
  }finally{app.close();}
});

const file = (id, name, extra = {}) => ({
  id, sha: 'a'.repeat(64), name, kind: 'document', size: 12, tags: [], starred: 0,
  added_at: stamp, updated_at: stamp, origin_path: `/Users/someone/docs/${name}`,
  source_name: '杂项', mode: 'managed', available: true, integrity: 'ok', verified_at: stamp,
  warning: '', media_error: '', has_thumbnail: false, excerpt: '', anchor: null,
  collections: [], ...extra
});

async function boot({ sources = [], jobs = [], impact, files = [], detail, fileImpact, collections = [], folded = [], nativePreferences, mediaCounts = {}, responder } = {}) {
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'http://127.0.0.1:9999/' });
  const { window } = dom;
  window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  window.HTMLDialogElement.prototype.close = function () {
    // A real <dialog>.close() on a closed dialog is a no-op and fires no event.
    // Without this guard the polyfill recurses forever through the close handler.
    if (!this.open) return;
    this.open = false; this.dispatchEvent(new window.Event('close'));
  };
  const calls = [];
  window.fetch = async (url, options = {}) => {
    const route = String(url);
    const body = options.body ? JSON.parse(options.body) : undefined;
    calls.push({ route, body });
    let data = {};
    if (route.startsWith('/api/status')) { data = status(sources, jobs, collections); Object.assign(data.stats.counts, mediaCounts); }
    else if (/^\/api\/files\/[^/?]+$/.test(route)) data = detail || file(route.split('/').pop(), 'a.md');
    else if (route==='/api/knowledge/profiles') data=[{id:'default',name:'默认 AI 访问',enabled:1,all_files:1,source_ids:[],collection_ids:[]}];
    else if (route.startsWith('/api/note?')) data={body:'',updated_at:''};
    else if (route==='/api/health') data={offline:[],unique:[],changed:[],missing:[],failed_jobs:0,last_backup:null,auto_backup:true,disk_free:100000000};
    else if (['/api/trash','/api/backups','/api/searches'].includes(route)) data=[];
    else if (route.startsWith('/api/files')) data = { files, total: files.length, elapsed_ms: 0 };
    else if (route.includes('/source/impact')) data = impact || { source: sources[0], files: 2, objects: 2, shared: 0, removed: 2, bytes: 2048 };
    else if (route.includes('/source/remove')) data = { files: 2, objects: 2, bytes: 2048, cancelled_jobs: 0 };
    else if (route.includes('/file/impact')) data = fileImpact || { files: 1, objects: 1, bytes: 12, shared: 0, offline: 0, unique: 0 };
    else if (route.includes('/file/remove')) data = { files: 1, objects: 1, bytes: 12 };
    else if (route.includes('/job/delete')) data = { state: 'done', kind: 'import' };
    else if (route.includes('/collection/create')) data = { id: 'c-new', name: body.name, count: 0 };
    else if (route.includes('/collection/rename')) data = { id: body.collection_id, name: body.name };
    else if (route.includes('/collection/delete')) data = { name: collections[0]?.name || '', files: collections[0]?.count || 0 };
    else if (route.includes('/collection/assign')) {
      const target = collections.find(c => c.id === body.collection_id) || { id: 'c-new', name: body.name, count: 0 };
      data = { collection: { ...target, count: (target.count || 0) + (body.action === 'remove' ? -1 : 1) }, changed: 1, action: body.action || 'add', created: !body.collection_id };
    }
    if (responder) { const override = await responder(route, body, calls); if (override !== undefined) data = override; }
    return { ok: true, json: async () => data };
  };
  for (const key of folded) window.localStorage.setItem('shiyue.fold.' + key, '1');
  const nativeCalls = [];
  if (nativePreferences) { window.shiyuePreferences = nativePreferences; window.webkit = { messageHandlers: { native: { postMessage: value => nativeCalls.push(value) } } }; }
  window.eval(app);
  const flush = async (turns = 6) => { for (let i = 0; i < turns; i++) await new Promise(r => window.setTimeout(r, 0)); };
  await flush();
  return { window, doc: window.document, calls, nativeCalls, flush, close: () => window.close() };
}

test('每个来源都有断开入口，确认前不会调用删除接口', async () => {
  const app = await boot({ sources: [source('s1', '杂项', 2), source('s2', '其他', 1)] });
  try {
    const rows = app.doc.querySelectorAll('#sources .source-item');
    assert.equal(rows.length, 2);
    assert.ok(rows[0].querySelector('[data-remove-source]'), '侧栏每一项都要能直接断开');
    assert.equal(app.doc.querySelector('#source-tools').hidden, true, '未选中来源时不显示工具条');

    rows[0].querySelector('[data-remove-source]').click();
    await app.flush();

    const dialog = app.doc.querySelector('#confirm-dialog');
    assert.equal(dialog.open, true, '必须弹出页内确认框（WKWebView 里 window.confirm 无效）');
    assert.match(app.doc.querySelector('#confirm-message').textContent, /Users\/someone\/杂项/);
    const detail = app.doc.querySelector('#confirm-detail').textContent;
    assert.match(detail, /2 份资料索引会移入回收站/);
    assert.match(detail, /保留托管副本与预览，占用 2\.0 KB/);
    assert.match(detail, /原始位置的文件不会被删除或移动/, '必须说清不会动原文件');

    assert.equal(app.calls.filter(c => c.route.includes('/source/remove')).length, 0, '确认之前不能删除');
    app.doc.querySelector('#confirm-cancel').click();
    await app.flush();
    assert.equal(dialog.open, false);
    assert.equal(app.calls.filter(c => c.route.includes('/source/remove')).length, 0, '取消之后也不能删除');
  } finally { app.close(); }
});

test('确认后断开来源，并切回全部资料', async () => {
  const app = await boot({ sources: [source('s1', '杂项', 2), source('s2', '其他', 1)] });
  try {
    // Selecting the source reveals the toolbar, which is the second entry point.
    app.doc.querySelector('#sources .source-item').click();
    await app.flush();
    assert.equal(app.doc.querySelector('#source-tools').hidden, false);
    assert.equal(app.doc.querySelector('#remove-source-button').hidden, false);

    app.doc.querySelector('#remove-source-button').click();
    await app.flush();
    assert.equal(app.doc.querySelector('#confirm-dialog').open, true);
    app.doc.querySelector('#confirm-ok').click();
    await app.flush();

    const removed = app.calls.filter(c => c.route.includes('/source/remove'));
    assert.equal(removed.length, 1);
    assert.deepEqual(removed[0].body, { source_id: 's1' });
    assert.equal(app.doc.querySelector('#page-title').textContent, '全部资料', '断开当前来源后应回到全部资料');
    assert.equal(app.doc.querySelector('#source-tools').hidden, true);
    assert.equal(app.doc.querySelector('#confirm-dialog').open, false);
  } finally { app.close(); }
});

test('共享内容会在确认框里说明会保留', async () => {
  const impact = { source: source('s1', '杂项', 3), files: 3, objects: 3, shared: 1, removed: 2, bytes: 0 };
  const app = await boot({ sources: [source('s1', '杂项', 3)], impact });
  try {
    app.doc.querySelector('[data-remove-source]').click();
    await app.flush();
    const detail = app.doc.querySelector('#confirm-detail').textContent;
    assert.match(detail, /1 个对象仍被其他来源使用，会保留/);
    assert.match(detail, /不占用资料库空间/, '就地索引不应声称释放空间');
  } finally { app.close(); }
});

test('后台任务有删除按钮，运行中的任务写明会立即停止', async () => {
  const jobs = [
    { id: 'j1', kind: 'import', state: 'running', total: 10, done: 2, error: null, result: null, created_at: stamp, updated_at: stamp },
    { id: 'j2', kind: 'verify', state: 'done', total: 3, done: 3, error: null, result: { verified: 3, missing: 0, changed: [] }, created_at: stamp, updated_at: stamp },
    { id: 'j3', kind: 'refresh', state: 'failed', total: 1, done: 0, error: '原件离线', result: null, created_at: stamp, updated_at: stamp }
  ];
  const app = await boot({ sources: [source('s1', '杂项', 2)], jobs });
  try {
    assert.equal(app.doc.querySelector('[data-delete-job="j1"]').textContent, '取消并删除');
    assert.equal(app.doc.querySelector('[data-delete-job="j2"]').textContent, '删除');
    assert.ok(app.doc.querySelector('[data-delete-job="j3"]'), '失败的任务也要能删掉');

    app.doc.querySelector('[data-delete-job="j1"]').click();
    await app.flush();
    assert.equal(app.doc.querySelector('#confirm-dialog').open, true);
    assert.match(app.doc.querySelector('#confirm-title').textContent, /取消并删除/);
    assert.match(app.doc.querySelector('#confirm-detail').textContent, /已经导入的资料会留在资料库/);

    app.doc.querySelector('#confirm-ok').click();
    await app.flush();
    const deleted = app.calls.filter(c => c.route.includes('/job/delete'));
    assert.equal(deleted.length, 1);
    assert.deepEqual(deleted[0].body, { job_id: 'j1' });
  } finally { app.close(); }
});

test('已完成的任务删除时不提示会中断', async () => {
  const jobs = [{ id: 'j2', kind: 'verify', state: 'done', total: 3, done: 3, error: null, result: { verified: 3, missing: 0, changed: [] }, created_at: stamp, updated_at: stamp }];
  const app = await boot({ sources: [source('s1', '杂项', 2)], jobs });
  try {
    app.doc.querySelector('[data-delete-job="j2"]').click();
    await app.flush();
    assert.match(app.doc.querySelector('#confirm-title').textContent, /删除这条任务记录/);
    assert.doesNotMatch(app.doc.querySelector('#confirm-detail').textContent, /下一个检查点/);
  } finally { app.close(); }
});

test('详情面板能移除单份资料，原件已丢失时明确警告', async () => {
  const row = file('f1', 'gone.md', { available: false, mode: 'managed' });
  const app = await boot({
    sources: [source('s1', '杂项', 1)], files: [row], detail: { ...row, chunks: [], versions: [] },
    fileImpact: { files: 1, objects: 1, bytes: 12, shared: 0, offline: 0, unique: 1 }
  });
  try {
    app.doc.querySelector('[data-file="f1"]').click();
    await app.flush();
    const button = app.doc.querySelector('#remove-file');
    assert.ok(button, '详情里必须有移除入口，原件删掉之后也要能用');
    assert.match(app.doc.querySelector('#detail').textContent, /原件已离线/);

    button.click();
    await app.flush();
    assert.equal(app.doc.querySelector('#confirm-dialog').open, true);
    assert.match(app.doc.querySelector('#confirm-detail').textContent, /唯一一份/);
    assert.match(app.doc.querySelector('#confirm-detail').textContent, /回收站后仍可恢复/);

    app.doc.querySelector('#confirm-ok').click();
    await app.flush();
    const removed = app.calls.filter(c => c.route.includes('/file/remove'));
    assert.equal(removed.length, 1);
    assert.deepEqual(removed[0].body, { ids: ['f1'] });
    assert.equal(app.doc.querySelector('#detail').hidden, true, '移除后详情应当关闭');
  } finally { app.close(); }
});

test('多选后可以一次移除所选资料', async () => {
  const rows = [file('f1', 'a.md'), file('f2', 'b.md')];
  const app = await boot({
    sources: [source('s1', '杂项', 2)], files: rows,
    fileImpact: { files: 2, objects: 2, bytes: 24, shared: 0, offline: 1, unique: 0 }
  });
  try {
    assert.equal(app.doc.querySelector('#remove-selection').hidden, true, '没有选择时不显示');
    for (const id of ['f1', 'f2']) {
      app.doc.querySelector(`[data-select="${id}"]`).click();
      await app.flush(1);
    }
    const bulk = app.doc.querySelector('#remove-selection');
    assert.equal(bulk.hidden, false, '选中资料后出现移除入口');
    assert.equal(bulk.textContent, '移除所选');

    bulk.click();
    await app.flush();
    assert.match(app.doc.querySelector('#confirm-title').textContent, /移除 2 份资料/);
    assert.match(app.doc.querySelector('#confirm-detail').textContent, /1 份资料的原件已不在/);

    app.doc.querySelector('#confirm-ok').click();
    await app.flush();
    const removed = app.calls.filter(c => c.route.includes('/file/remove'));
    assert.equal(removed.length, 1);
    assert.deepEqual(removed[0].body.ids.slice().sort(), ['f1', 'f2']);
  } finally { app.close(); }
});

test('托管副本还在但原件已删除时，详情要说明这是最后一份', async () => {
  const row = file('f9', 'kept.md', { available: true, origin_missing: true });
  const app = await boot({
    sources: [source('s1', '杂项', 1)], files: [row], detail: { ...row, chunks: [], versions: [] },
    fileImpact: { files: 1, objects: 1, bytes: 12, shared: 0, offline: 0, unique: 1 }
  });
  try {
    app.doc.querySelector('[data-file="f9"]').click();
    await app.flush();
    assert.match(app.doc.querySelector('#detail').textContent, /唯一一份/);
    assert.doesNotMatch(app.doc.querySelector('#detail').textContent, /原始位置的文件不受影响/);
  } finally { app.close(); }
});

/* ---- 0.3 自定义分类 ---- */

test('侧栏分类可点击筛选，页面标题与工具条随之切换', async () => {
  const rows = [file('f1', 'a.md'), file('f2', 'b.md')];
  const app = await boot({ sources: [source('s1', '杂项', 2)], collections: [collection('c1', 'AI资料', 2)], files: rows });
  try {
    const item = app.doc.querySelector('#collections [data-collection="c1"]');
    assert.ok(item, '侧栏必须有分类条目');
    assert.match(item.textContent, /AI资料/);
    assert.equal(item.querySelector('b').textContent, '2');

    item.click();
    await app.flush(10);
    const files = app.calls.filter(c => c.route.startsWith('/api/files?'));
    assert.match(files[files.length - 1].route, /collection=c1/, '筛选必须走服务端，不能只在前端过滤一页');
    assert.equal(app.doc.querySelector('#page-title').textContent.trim(), 'AI资料');
    assert.equal(app.doc.querySelector('#collection-tools').hidden, false);
    assert.match(app.doc.querySelector('#collection-info').textContent, /原件位置不变/);
    assert.equal(app.doc.querySelector('#source-tools').hidden, true, '分类和来源的工具条不能同时出现');
  } finally { app.close(); }
});

test('详情里可以把资料加入已有分类', async () => {
  const row = file('f1', 'paper.md');
  const app = await boot({
    sources: [source('s1', '杂项', 1)], collections: [collection('c1', 'AI资料', 1)],
    files: [row], detail: { ...row, chunks: [], versions: [], collections: [] }
  });
  try {
    app.doc.querySelector('[data-file="f1"]').click();
    await app.flush();
    const add = app.doc.querySelector('#add-to-collection');
    assert.ok(add, '详情必须有加入分类的入口');
    assert.match(app.doc.querySelector('#detail').textContent, /还没有归入分类/);

    add.click();
    await app.flush();
    assert.equal(app.doc.querySelector('#collection-dialog').open, true);
    const choice = app.doc.querySelector('[data-pick-collection="c1"]');
    assert.ok(choice, '选择框里要列出已有分类');
    choice.click();
    await app.flush(10);

    const assign = app.calls.filter(c => c.route.includes('/collection/assign'));
    assert.equal(assign.length, 1);
    assert.deepEqual(assign[0].body, { ids: ['f1'], collection_id: 'c1' });
    assert.equal(app.doc.querySelector('#collection-dialog').open, false);
  } finally { app.close(); }
});

test('选中资料后可以新建分类并直接放进去', async () => {
  const rows = [file('f1', 'a.md'), file('f2', 'b.md')];
  const app = await boot({ sources: [source('s1', '杂项', 2)], collections: [], files: rows });
  try {
    for (const id of ['f1', 'f2']) {
      app.doc.querySelector(`[data-select="${id}"]`).click();
      await app.flush(1);
    }
    const bulk = app.doc.querySelector('#collect-selection');
    assert.equal(bulk.hidden, false);
    assert.equal(bulk.textContent, '加入分类');

    bulk.click();
    await app.flush();
    assert.match(app.doc.querySelector('#collection-title').textContent, /把 2 份资料加入分类/);
    assert.match(app.doc.querySelector('#collection-choices').textContent, /还没有分类/);

    app.doc.querySelector('#new-collection-name').value = 'AI资料';
    app.doc.querySelector('#create-collection').click();
    await app.flush(10);

    const assign = app.calls.filter(c => c.route.includes('/collection/assign'));
    assert.equal(assign.length, 1);
    assert.deepEqual(assign[0].body, { ids: ['f1', 'f2'], name: 'AI资料' });
    assert.equal(app.calls.filter(c => c.route.includes('/collection/create')).length, 0, '新建并加入应当一次请求完成');
  } finally { app.close(); }
});

test('详情里可以从分类中移除，文件本身不受影响', async () => {
  const row = file('f1', 'a.md', { collections: [{ id: 'c1', name: 'AI资料' }] });
  const app = await boot({
    sources: [source('s1', '杂项', 1)], collections: [collection('c1', 'AI资料', 1)],
    files: [row], detail: { ...row, chunks: [], versions: [] }
  });
  try {
    app.doc.querySelector('[data-file="f1"]').click();
    await app.flush();
    assert.match(app.doc.querySelector('#detail').textContent, /AI资料/);
    const chip = app.doc.querySelector('[data-leave-collection="c1"]');
    assert.ok(chip, '分类标签上要能移除');
    chip.click();
    await app.flush(10);

    const call = app.calls.filter(c => c.route.includes('/collection/assign')).pop();
    assert.deepEqual(call.body, { collection_id: 'c1', ids: ['f1'], action: 'remove' });
    assert.equal(app.calls.filter(c => c.route.includes('/file/remove')).length, 0, '移出分类不等于删除资料');
  } finally { app.close(); }
});

test('删除分类要确认，并说明资料与原件不受影响', async () => {
  const app = await boot({ sources: [source('s1', '杂项', 2)], collections: [collection('c1', 'AI资料', 2)], files: [] });
  try {
    app.doc.querySelector('#collections [data-collection="c1"]').click();
    await app.flush(10);
    assert.equal(app.doc.querySelector('#collection-tools').hidden, false);

    app.doc.querySelector('#delete-collection-button').click();
    await app.flush();
    assert.equal(app.doc.querySelector('#confirm-dialog').open, true);
    assert.match(app.doc.querySelector('#confirm-title').textContent, /删除分类「AI资料」/);
    assert.match(app.doc.querySelector('#confirm-detail').textContent, /资料、标签与原件继续保留/);
    assert.equal(app.calls.filter(c => c.route.includes('/collection/delete')).length, 0, '确认之前不能删');

    app.doc.querySelector('#confirm-ok').click();
    await app.flush(10);
    assert.equal(app.calls.filter(c => c.route.includes('/collection/delete')).length, 1);
  } finally { app.close(); }
});

test('分类可以重命名', async () => {
  const app = await boot({ sources: [source('s1', '杂项', 1)], collections: [collection('c1', 'AI资料', 1)], files: [] });
  try {
    app.doc.querySelector('#collections [data-collection="c1"]').click();
    await app.flush(10);
    app.doc.querySelector('#rename-collection').click();
    await app.flush();

    const input = app.doc.querySelector('#rename-input');
    assert.equal(input.value, 'AI资料', '重命名要带出当前名称');
    input.value = '机器学习';
    app.doc.querySelector('#rename-save').click();
    await app.flush(10);

    const call = app.calls.filter(c => c.route.includes('/collection/rename')).pop();
    assert.deepEqual(call.body, { collection_id: 'c1', name: '机器学习' });
  } finally { app.close(); }
});

test('选择框会标出资料已经所在的分类', async () => {
  const row = file('f1', 'a.md', { collections: [{ id: 'c1', name: 'AI资料' }] });
  const app = await boot({
    sources: [source('s1', '杂项', 1)], collections: [collection('c1', 'AI资料', 1)],
    files: [row], detail: { ...row, chunks: [], versions: [] }
  });
  try {
    app.doc.querySelector('[data-file="f1"]').click();
    await app.flush();
    app.doc.querySelector('#add-to-collection').click();
    await app.flush();
    assert.match(app.doc.querySelector('[data-pick-collection="c1"]').textContent, /已加入/);
  } finally { app.close(); }
});

/* ---- 0.3.1 侧栏分组：分类在前，两组都能折叠 ---- */

test('分类排在来源之前，两组各自折叠互不牵连', async () => {
  const app = await boot({ sources: [source('s1', '杂项', 2)], collections: [collection('c1', 'AI资料', 1)], files: [] });
  try {
    const groups = [...app.doc.querySelectorAll('.sidebar-scroll .section-toggle')].map(b => b.dataset.fold);
    assert.deepEqual(groups, ['collections', 'sources'], '分类要排在来源前面');

    const collectionsToggle = app.doc.querySelector('[data-fold="collections"]');
    const sourcesToggle = app.doc.querySelector('[data-fold="sources"]');
    assert.equal(collectionsToggle.querySelector('b').textContent, '1', '折叠后也要知道有几项');
    assert.equal(sourcesToggle.querySelector('b').textContent, '1');
    assert.equal(app.doc.querySelector('#collections').hidden, false);

    collectionsToggle.click();
    await app.flush(2);
    assert.equal(app.doc.querySelector('#collections').hidden, true);
    assert.equal(collectionsToggle.getAttribute('aria-expanded'), 'false');
    assert.equal(app.doc.querySelector('#sources').hidden, false, '折叠分类不能连带折叠来源');
    assert.equal(app.window.localStorage.getItem('shiyue.fold.collections'), '1', '折叠状态要记住');

    sourcesToggle.click();
    await app.flush(2);
    assert.equal(app.doc.querySelector('#sources').hidden, true);
    assert.equal(app.doc.querySelector('#collection-total').textContent, '1', '计数不因折叠消失');
  } finally { app.close(); }
});

test('重新打开时沿用上次的折叠状态', async () => {
  const app = await boot({
    sources: [source('s1', '杂项', 2)], collections: [collection('c1', 'AI资料', 1)], files: [],
    folded: ['collections']
  });
  try {
    assert.equal(app.doc.querySelector('#collections').hidden, true);
    assert.equal(app.doc.querySelector('[data-fold="collections"]').getAttribute('aria-expanded'), 'false');
    assert.equal(app.doc.querySelector('#sources').hidden, false, '只恢复被折叠的那一组');
  } finally { app.close(); }
});


test('首页只保留一个主要添加入口，统计和维护集中在迁移面板', async () => {
  const app = await boot({ sources: [source('s1', '杂项', 2)] });
  try {
    assert.equal(app.doc.querySelectorAll('.import-launch').length, 1);
    assert.ok(app.doc.querySelector('#storage-dialog #stats'));
    assert.ok(app.doc.querySelector('#storage-dialog #refresh-derived'));
    assert.equal(app.doc.querySelector('#stat-files').textContent, '2份');
    assert.equal(app.doc.querySelector('#media-filters').hidden, true);
    assert.equal(app.doc.querySelector('#filter-toggle').hidden, true);
    app.doc.querySelector('.import-launch').click();
    assert.equal(app.doc.querySelector('#import-dialog').open, true);
  } finally { app.close(); }
});

test('图片页展开拍摄筛选，返回文档后收起，也可手动切换', async () => {
  const app = await boot({ mediaCounts: { image: 2 } });
  try {
    const toggle = app.doc.querySelector('#filter-toggle');
    assert.equal(toggle.hidden, false);
    assert.equal(toggle.getAttribute('aria-expanded'), 'false');
    app.doc.querySelector('[data-view=image]').click(); await app.flush();
    assert.equal(app.doc.querySelector('#media-filters').hidden, false);
    assert.equal(app.doc.querySelector('#view-grid').getAttribute('aria-pressed'), 'true');
    toggle.click();
    assert.equal(app.doc.querySelector('#media-filters').hidden, true);
    app.doc.querySelector('[data-view=document]').click(); await app.flush();
    assert.equal(app.doc.querySelector('#media-filters').hidden, true);
    assert.equal(app.doc.querySelector('[data-view=document]').getAttribute('aria-current'), 'page');
    toggle.click();
    assert.equal(app.doc.querySelector('#media-filters').hidden, false);
  } finally { app.close(); }
});

test('取消批量选择会清掉计数、复选框和操作条', async () => {
  const app = await boot({ files: [file('f1', 'a.md'), file('f2', 'b.md')] });
  try {
    const select = app.doc.querySelector('#select-all'); select.checked = true;
    select.dispatchEvent(new app.window.Event('change'));
    assert.equal(app.doc.querySelector('#selection-count').textContent, '已选择 2 份');
    assert.equal(app.doc.querySelector('#selection-bar').hidden, false);
    app.doc.querySelector('#clear-selection').click();
    assert.equal(app.doc.querySelector('#selection-bar').hidden, true);
    assert.equal(select.checked, false);
    assert.equal(app.doc.querySelectorAll('[data-select]:checked').length, 0);
  } finally { app.close(); }
});

test('键盘进入来源删除按钮不会先切换来源，空格能打开来源', async () => {
  const app = await boot({ sources: [source('s1', '项目', 2)] });
  try {
    const start = app.calls.filter(c => c.route.startsWith('/api/files?')).length;
    app.doc.querySelector('[data-remove-source]').dispatchEvent(new app.window.KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));
    await app.flush();
    assert.equal(app.calls.filter(c => c.route.startsWith('/api/files?')).length, start);
    app.doc.querySelector('[data-source]').dispatchEvent(new app.window.KeyboardEvent('keydown', {key: ' ', bubbles: true, cancelable: true}));
    await app.flush();
    const last = app.calls.filter(c => c.route.startsWith('/api/files?')).pop();
    assert.equal(new URL(last.route, 'http://localhost').searchParams.get('source'), 's1');
  } finally { app.close(); }
});

test('详情限制后台焦点，嵌套确认框取消后保留详情，关闭后归还焦点', async () => {
  const app = await boot({ files: [file('f1', 'a.md')], detail: {...file('f1', 'a.md'), chunks: [], versions: []} });
  try {
    const row = app.doc.querySelector('[data-file]'); row.focus(); row.click(); await app.flush();
    assert.equal(app.doc.querySelector('main').inert, true);
    assert.equal(app.doc.activeElement.id, 'close-detail');
    assert.equal(app.doc.querySelector('.advanced-info').open, false);
    app.doc.querySelector('#remove-file').click(); await app.flush();
    app.doc.dispatchEvent(new app.window.KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
    assert.equal(app.doc.querySelector('#detail').hidden, false, '确认框上的 Escape 不应同时关闭详情');
    app.doc.querySelector('#confirm-cancel').click();
    app.doc.dispatchEvent(new app.window.KeyboardEvent('keydown', {key:'Escape', bubbles:true}));
    assert.equal(app.doc.querySelector('#detail').hidden, true);
    assert.equal(app.doc.querySelector('main').inert, false);
    assert.equal(app.doc.activeElement, row);
  } finally { app.close(); }
});

test('资料减少后当前页自动回退，不会停在不存在的第二页', async () => {
  let total = 61;
  const app = await boot({ responder: route => {
    if (!route.startsWith('/api/files?')) return;
    const offset = Number(new URL(route, 'http://localhost').searchParams.get('offset'));
    return {files: offset < total ? [file('f1', 'a.md')] : [], total, elapsed_ms:0};
  }});
  try {
    app.doc.querySelector('#next-page').click(); await app.flush();
    assert.equal(app.doc.querySelector('#page-number').textContent, '2 / 2');
    total = 1;
    app.doc.querySelector('#refresh-button').click(); await app.flush();
    assert.equal(app.doc.querySelector('#page-number').textContent, '1 / 1');
    assert.equal(app.doc.querySelectorAll('[data-file]').length, 1);
    assert.equal(app.doc.querySelector('#file-list').getAttribute('aria-busy'), 'false');
  } finally { app.close(); }
});

test('原生偏好覆盖随机端口的存储，折叠变化传回原生应用', async () => {
  const app = await boot({ nativePreferences: {sources:true, collections:false}, folded:['collections'] });
  try {
    assert.equal(app.doc.querySelector('#sources').hidden, true);
    assert.equal(app.doc.querySelector('#collections').hidden, false);
    app.doc.querySelector('[data-fold=sources]').click();
    assert.deepEqual(JSON.parse(JSON.stringify(app.nativeCalls.pop())), {action:'preference', key:'sources', folded:false});
  } finally { app.close(); }
});

test('搜索较慢的旧响应不能覆盖更新的结果', async () => {
  let finishOld;
  const app = await boot({ responder: route => {
    if(!route.startsWith('/api/files?')) return;
    const q = new URL(route, 'http://localhost').searchParams.get('q');
    if(q==='old')return new Promise(resolve=>finishOld=()=>resolve({files:[file('old','旧结果.md')], total:1, elapsed_ms:50}));
    if(q==='new')return {files:[file('new','新结果.md')], total:1, elapsed_ms:1};
  }});
  try {
    const search = app.doc.querySelector('#search');
    search.value='old'; search.dispatchEvent(new app.window.Event('input'));
    await new Promise(r=>app.window.setTimeout(r,200));
    search.value='new'; search.dispatchEvent(new app.window.Event('input'));
    await new Promise(r=>app.window.setTimeout(r,200)); await app.flush();
    finishOld(); await app.flush();
    assert.equal(app.doc.querySelector('[data-file]').dataset.file, 'new');
    assert.equal(app.doc.querySelector('#file-list').getAttribute('aria-busy'),'false');
  } finally { app.close(); }
});


test('回收站恢复资料，永久清理必须额外确认', async () => {
 const app=await boot({responder: route=>route==='/api/trash'?[{id:'t1',label:'测试资料',files:1,created_at:stamp}]:route==='/api/trash/restore'?{files:1}:undefined});
 try{
  app.doc.querySelector('#trash-button').click();await app.flush();
  app.doc.querySelector('[data-restore-trash]').click();await app.flush();
  assert.ok(app.calls.some(c=>c.route==='/api/trash/restore'&&c.body.id==='t1'));
  app.doc.querySelector('[data-purge-trash]').click();await app.flush();
  assert.equal(app.doc.querySelector('#confirm-dialog').open,true);
  assert.match(app.doc.querySelector('#confirm-message').textContent,/无法撤销/);
  assert.equal(app.calls.filter(c=>c.route==='/api/trash/purge').length,0);
  app.doc.querySelector('#confirm-cancel').click();await app.flush();
  assert.equal(app.calls.filter(c=>c.route==='/api/trash/purge').length,0);
 }finally{app.close();}
});

test('来源重定位先预览，编辑路径后必须重新验证',async()=>{
 const app=await boot({sources:[source('s1','项目',1)],responder: route=>route==='/api/source/relocate'?{matched:1,changed:[],missing:[]}:undefined});
 try{
  app.doc.querySelector('[data-source]').click();await app.flush();
  app.doc.querySelector('#relocate-source').click();
  assert.equal(app.doc.querySelector('#relocate-apply').disabled,true);
  app.doc.querySelector('#relocate-path').value='/tmp/new';
  app.doc.querySelector('#relocate-preview-button').click();await app.flush();
  assert.equal(app.doc.querySelector('#relocate-apply').disabled,false);
  app.doc.querySelector('#relocate-path').dispatchEvent(new app.window.Event('input'));
  assert.equal(app.doc.querySelector('#relocate-apply').disabled,true);
  assert.equal(app.calls.filter(c=>c.route==='/api/source/relocate'&&c.body.apply).length,0);
 }finally{app.close();}
});

test('健康问题可打开详情，备份设置有真实提交',async()=>{
 const app=await boot({detail:{...file('f1','离线.md'),chunks:[],versions:[]},responder:route=>route==='/api/health'?{offline:[{id:'f1',name:'离线.md'}],unique:[],missing:[],changed:[],last_backup:null,failed_jobs:0,disk_free:10000,auto_backup:true}:undefined});
 try{
  app.doc.querySelector('#health-button').click();await app.flush();
  app.doc.querySelector('[data-health-file]').click();await app.flush();
  assert.equal(app.doc.querySelector('#detail').hidden,false);
  app.doc.querySelector('#close-detail').click();
  app.doc.querySelector('#backup-manager').click();await app.flush();
  const cb=app.doc.querySelector('#auto-backup');cb.checked=false;cb.dispatchEvent(new app.window.Event('change'));await app.flush();
  assert.ok(app.calls.some(c=>c.route==='/api/backup/settings'&&c.body.enabled===false));
 }finally{app.close();}
});

test('资料健康显示外部原件备份缺口与最近恢复校验',async()=>{
 const app=await boot({responder:route=>route==='/api/health'?{offline:[],unique:[],missing:[],changed:[],last_backup:null,failed_jobs:0,disk_free:10000,auto_backup:true,backup_coverage:{managed_objects:8,managed_bytes:914345,external_files:31,external_bytes:13833363089},last_restore_verification:{verified_at:'2026-11-07T12:00:00Z'}}:undefined});
 try{
  app.doc.querySelector('#health-button').click();await app.flush();
  const text=app.doc.querySelector('#health-body').textContent;
  assert.ok(text.includes('31 条资料记录的外部原件未纳入库内备份'));
  assert.ok(text.includes('最近恢复校验：'));
  assert.ok(!text.includes('最近恢复校验：尚无记录'));
 }finally{app.close();}
});

test('组合搜索传递精确标签与日期，保存和载入智能分类',async()=>{
 const app=await boot({responder:route=>route==='/api/searches'?[{id:'saved1',name:'项目资料',filters:{q:'合同',tag:'项目',after:'2026-01-01'}}]:undefined});
 try{
  app.doc.querySelector('#search-options').click();await app.flush();
  app.doc.querySelector('#tag-filter').value='项目';app.doc.querySelector('#after-filter').value='2026-01-01';
  app.doc.querySelector('#apply-advanced').click();await app.flush();
  let url=new URL(app.calls.filter(c=>c.route.startsWith('/api/files?')).pop().route,'http://localhost');
  assert.equal(url.searchParams.get('tag'),'项目');assert.equal(url.searchParams.get('after'),'2026-01-01');
  app.doc.querySelector('#search-options').click();await app.flush();
  app.doc.querySelector('[data-load-search]').click();await app.flush();
  url=new URL(app.calls.filter(c=>c.route.startsWith('/api/files?')).pop().route,'http://localhost');
  assert.equal(url.searchParams.get('q'),'合同');assert.equal(url.searchParams.get('tag'),'项目');
  app.doc.querySelector('[data-view=all]').click();await app.flush();
  url=new URL(app.calls.filter(c=>c.route.startsWith('/api/files?')).pop().route,'http://localhost');assert.equal(url.searchParams.get('tag'),'');
 }finally{app.close();}
});

test('PDF 阅读恢复上次页码并提交翻页位置，OCR 单独排队',async()=>{
 const app=await boot({files:[file('f1','扫描.pdf')],detail:{...file('f1','扫描.pdf'),chunks:[],versions:[]},responder:route=>route==='/api/files/f1/reader'?{pages:5,page:3}:undefined});
 try{
  app.doc.querySelector('[data-file]').click();await app.flush();
  app.doc.querySelector('#read-inside').click();await app.flush();
  assert.equal(app.doc.querySelector('#reader-page').value,'3');
  app.doc.querySelector('#reader-next').click();await app.flush();
  assert.ok(app.calls.some(c=>c.route==='/api/reading'&&c.body.page===4));
  app.doc.querySelector('#page-reader').close();app.doc.querySelector('#ocr-file').click();await app.flush();
  assert.ok(app.calls.some(c=>c.route==='/api/ocr'&&c.body.id==='f1'));
 }finally{app.close();}
});

test('原生拖入显示路径并去重，默认保留原位置，确认前不导入',async()=>{
 const app=await boot();try{
  app.window.dispatchEvent(new app.window.CustomEvent('native-drop',{detail:{paths:['/tmp/资料夹','/tmp/a.md','/tmp/a.md']}}));await app.flush();
  assert.equal(app.doc.querySelector('#import-dialog').open,true);
  assert.equal(app.doc.querySelector('#import-paths').value,'/tmp/资料夹\n/tmp/a.md');
  assert.equal(app.doc.querySelector('input[name=mode]:checked').value,'in-place');
  assert.equal(app.calls.filter(c=>c.route==='/api/import').length,0);
  app.doc.querySelector('#start-import').click();await app.flush();
  assert.ok(app.calls.some(c=>c.route==='/api/import'&&c.body.mode==='in-place'&&c.body.paths.length===2));
 }finally{app.close();}
});
test('拒绝非本机拖入与换行歧义，其他弹窗打开时不劫持',async()=>{
 const app=await boot();try{
  for(const paths of [['https://example.com'],['/tmp/a\nb']])app.window.dispatchEvent(new app.window.CustomEvent('native-drop',{detail:{paths}}));
  assert.equal(app.doc.querySelector('#import-dialog').open,false);
  app.doc.querySelector('#storage-button').click();
  app.window.dispatchEvent(new app.window.CustomEvent('native-drop',{detail:{paths:['/tmp/a.md']}}));
  assert.equal(app.doc.querySelector('#import-dialog').open,false);
 }finally{app.close();}
});
test('AI 知识库面板获取只读能力和当前库的接入配置',async()=>{
 const app=await boot({responder:route=>route.startsWith('/api/knowledge/info')?{files:3,sources:1}:route.startsWith('/api/knowledge/config')?{mcpServers:{shiyue:{command:'/python',args:['/knowledge_mcp.py','--data','/tmp/library']}}}:undefined});
 try{app.doc.querySelector('#knowledge-button').click();await app.flush();assert.equal(app.doc.querySelector('#knowledge-dialog').open,true);assert.match(app.doc.querySelector('#knowledge-summary').textContent,/3 份资料/);assert.equal(JSON.parse(app.doc.querySelector('#knowledge-config').value).mcpServers.shiyue.args.at(-1),'/tmp/library');}finally{app.close();}
});

test('AI 范围可以限定来源并撤销，空范围不会自动变成全部', async()=>{
 const profiles=[{id:'default',name:'默认 AI 访问',enabled:1,all_files:1,source_ids:[],collection_ids:[]},{id:'limited',name:'项目',enabled:1,all_files:0,source_ids:['s1'],collection_ids:[]}];
 const app=await boot({sources:[source('s1','公开',1),source('s2','私人',1)],responder:(route,body)=>{
  if(route==='/api/knowledge/profiles')return profiles;
  if(route.startsWith('/api/knowledge/info'))return {files:1,passages:8};
  if(route.startsWith('/api/knowledge/config'))return {mcpServers:{shiyue:{args:['--profile','limited']}}};
  if(route==='/api/knowledge/profile'){Object.assign(profiles.find(p=>p.id===body.id),body);return {id:body.id};}
 }});
 try{
  app.doc.querySelector('#knowledge-button').click();await app.flush();const select=app.doc.querySelector('#knowledge-profile');select.value='limited';select.dispatchEvent(new app.window.Event('change'));await app.flush();
  assert.equal(app.doc.querySelector('#profile-all').checked,false);
  assert.equal(app.doc.querySelector('[data-scope-source="s1"]').checked,true);
  assert.equal(app.doc.querySelector('[data-scope-source="s2"]').checked,false);
  app.doc.querySelector('#profile-enabled').checked=false;app.doc.querySelector('#save-knowledge-profile').click();await app.flush();
  const save=app.calls.find(c=>c.route==='/api/knowledge/profile');assert.equal(save.body.enabled,false);assert.deepEqual(save.body.source_ids,['s1']);assert.equal(save.body.all_files,false);
  assert.match(app.doc.querySelector('#knowledge-summary').textContent,/撤销/);
 }finally{app.close();}
});

test('自动更新开关和笔记保存调用实际接口',async()=>{
 const s=source('s1','项目',1);s.watch={enabled:1};
 const app=await boot({sources:[s],files:[file('f1','note.md')],detail:file('f1','note.md',{chunks:[],versions:[]}),responder:(route,body)=>route.startsWith('/api/note?')?{body:'旧笔记',updated_at:'v1'}:route==='/api/note'?{body:body.body,updated_at:'v2'}:undefined});
 try{
  app.doc.querySelector('[data-source="s1"]').click();await app.flush();assert.equal(app.doc.querySelector('#source-watch').checked,true);
  const watch=app.doc.querySelector('#source-watch');watch.checked=false;watch.dispatchEvent(new app.window.Event('change'));await app.flush();
  assert.deepEqual(app.calls.find(c=>c.route==='/api/source/watch').body,{source_id:'s1',enabled:false});
  app.doc.querySelector('[data-file="f1"]').click();await app.flush();
  const note=app.doc.querySelector('.note-editor textarea');assert.equal(note.value,'旧笔记');note.value='新的理解';app.doc.querySelector('.note-editor button').click();await app.flush();
  assert.deepEqual(app.calls.find(c=>c.route==='/api/note').body,{id:'f1',body:'新的理解',expected_updated_at:'v1'});
 }finally{app.close();}
});

test('增量备份先计算范围，改动位置使旧估算失效',async()=>{
 const app=await boot({responder:route=>route==='/api/repository/plan'?{objects:2,total_bytes:100,new_bytes_estimate:50,reuse_bytes_estimate:50,database_bytes:10,free_bytes:1e9,same_volume:true,notice:'SHA 校验'}:undefined});
 try{
  app.doc.querySelector('#repository-path').value='/Volumes/backup/shiyue';app.doc.querySelector('#repository-external').checked=true;
  app.doc.querySelector('#repository-plan').click();await app.flush();assert.equal(app.doc.querySelector('#repository-start').disabled,false);
  assert.match(app.doc.querySelector('#repository-summary').textContent,/同一卷/);
  app.doc.querySelector('#repository-start').click();await app.flush();assert.deepEqual(app.calls.find(c=>c.route==='/api/repository/backup').body,{path:'/Volumes/backup/shiyue',include_external:true});
  app.doc.querySelector('#repository-path').value='/different';app.doc.querySelector('#repository-path').dispatchEvent(new app.window.Event('input'));assert.equal(app.doc.querySelector('#repository-start').disabled,true);
 }finally{app.close();}
});

test('固定证据使用单层弹窗，返回检索保留查询',async()=>{
 const pid='a'.repeat(64);
 const app=await boot({responder:route=>{
  if(route.startsWith('/api/knowledge/info'))return {files:1,passages:2};
  if(route.startsWith('/api/knowledge/config'))return {mcpServers:{}};
  if(route.startsWith('/api/knowledge/search'))return {results:[{id:'f1',title:'原文.md',passage_id:pid,snippet:'人工智能应用'}]};
  if(route.startsWith('/api/citation?'))return {id:'f1',title:'原文.md',historical:false,passages:[{id:pid,text:'原文 <script>不会执行</script>',label:'正文',is_target:true,citation:'shiyue://files/f1/passages/'+pid}]};
 }});
 try{
  app.doc.querySelector('#knowledge-button').click();await app.flush();app.doc.querySelector('#knowledge-query').value='人工智能应用';
  app.doc.querySelector('#knowledge-search-form').dispatchEvent(new app.window.Event('submit',{bubbles:true,cancelable:true}));await app.flush();
  app.doc.querySelector('[data-evidence-file]').click();await app.flush();
  assert.equal(app.doc.querySelector('#knowledge-dialog').open,true);assert.equal(app.doc.querySelector('#knowledge-search-view').hidden,true);assert.equal(app.doc.querySelector('#evidence-view').hidden,false);
  assert.equal(app.doc.activeElement.id,'evidence-back');
  assert.equal(app.doc.querySelectorAll('#evidence-body script').length,0);assert.match(app.doc.querySelector('#evidence-body').textContent,/<script>不会执行<\/script>/);
  app.doc.querySelector('#evidence-back').click();assert.equal(app.doc.querySelector('#evidence-view').hidden,true);assert.equal(app.doc.querySelector('#knowledge-search-view').hidden,false);assert.equal(app.doc.querySelector('#knowledge-dialog').open,true);
  assert.equal(app.doc.activeElement.id,'knowledge-query');
  assert.equal(app.doc.querySelector('#knowledge-query').value,'人工智能应用');
 }finally{app.close();}
});

test('磁盘发现只列候选，选择后仅提交具体ID并跳过已入库文件',async()=>{
 const app=await boot({responder:route=>{
  if(route==='/api/discovery/locations')return {locations:[{path:'/data',name:'资料盘'}],defaults:['/data']};
  if(route==='/api/discovery/start')return {id:'scan1'};
  if(route==='/api/discovery/preview')return {plan_id:'plan1',can_apply:true,selected:1,matched:0,notice:'只追加',items:[{id:'a',name:'<unsafe>.md',path:'/data/<unsafe>.md',collections:[],tags:[],rules:[],conflicts:[]}]};
  if(route.startsWith('/api/discovery/scan?'))return {state:'done',total:2,filtered_total:2,has_more:false,items:[{id:'a',path:'/data/<unsafe>.md',name:'<unsafe>.md',kind:'document',size:42,indexed:false},{id:'b',path:'/data/旧文.md',name:'旧文.md',kind:'document',size:8,indexed:true}]};
 }});
 try{
  app.doc.querySelector('#discover-button').click();await app.flush();assert.equal(app.calls.some(c=>c.route==='/api/discovery/start'),false);
  app.doc.querySelector('#discovery-start').click();await app.flush();
  assert.deepEqual(app.calls.find(c=>c.route==='/api/discovery/start').body.paths,['/data']);
  assert.equal(app.calls.some(c=>c.route==='/api/discovery/import'),false);assert.equal(app.doc.querySelector('#discovery-results unsafe'),null);
  assert.equal(app.doc.querySelector('[data-candidate="b"]').disabled,true);
  const check=app.doc.querySelector('#discovery-select-page');check.checked=true;check.dispatchEvent(new app.window.Event('change'));app.doc.querySelector('#discovery-import').click();await app.flush();
  assert.equal(app.calls.some(c=>c.route==='/api/discovery/import'),false);assert.equal(app.doc.querySelector('#classification-preview-dialog').open,true);assert.equal(app.doc.querySelector('#classification-preview-items unsafe'),null);
  app.doc.querySelector('#classification-confirm').click();await app.flush();
  const call=app.calls.find(c=>c.route==='/api/discovery/import');assert.equal(call.body.plan_id,'plan1');assert.deepEqual(call.body.ids,['a']);assert.equal(call.body.mode,'in-place');assert.ok(call.body.request_id);assert.equal(app.doc.querySelector('#discovery-dialog').open,false);
 }finally{app.close();}
});

test('扫描运行时可以勾选，但停止或结束后才能入库',async()=>{
 const app=await boot({responder:route=>route==='/api/discovery/locations'?{locations:[],defaults:[]}:route==='/api/discovery/start'?{id:'scan2'}:route.startsWith('/api/discovery/scan?')?{state:'running',total:1,filtered_total:1,has_more:false,items:[{id:'a',path:'/data/a.md',name:'a.md',size:4,indexed:false}]}:undefined});
 try{app.doc.querySelector('#discover-button').click();await app.flush();app.doc.querySelector('#discovery-paths').value='/data';app.doc.querySelector('#discovery-start').click();await app.flush();const item=app.doc.querySelector('[data-candidate]');item.checked=true;item.dispatchEvent(new app.window.Event('change',{bubbles:true}));assert.equal(app.doc.querySelector('#discovery-import').disabled,true);assert.equal(app.doc.querySelector('#discovery-cancel').disabled,false);app.doc.querySelector('#discovery-dialog').close();}finally{app.close();}
});

test('Agent命令包含独立写入CLI、等待时间与安全引用的本机路径',async()=>{
 const app=await boot({responder:route=>route==='/api/ingest/config'?{command:'/app Python/python',script:'/app/agent_ingest.py',data:"/data/it's local"}:undefined});
 try{app.doc.querySelector('#load-ingest-command').click();await app.flush();const value=app.doc.querySelector('#ingest-command').value;assert.match(value,/--wait 120/);assert.match(value,/agent_ingest\.py/);assert.ok(value.includes("'\\''"));assert.ok(!value.includes('token'));}finally{app.close();}
});


test('分类树支持折叠、键盘展开与新建子分类', async () => {
  const cols=[
    {...collection('root','经学',2),parent_id:null,depth:1,path:['经学'],path_label:'经学',child_count:1,direct_count:0},
    {...collection('child','易学',2),parent_id:'root',depth:2,path:['经学','易学'],path_label:'经学 / 易学',child_count:0,direct_count:2}
  ];
  const app=await boot({collections:cols});
  try {
    assert.equal(app.doc.querySelector('[data-collection="child"]').getAttribute('aria-level'),'2');
    app.doc.querySelector('[data-toggle-collection="root"]').click();
    assert.equal(app.doc.querySelector('[data-collection="child"]'),null);
    const root=app.doc.querySelector('[data-collection="root"]');
    root.dispatchEvent(new app.window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    assert.ok(app.doc.querySelector('[data-collection="child"]'));
    app.doc.querySelector('[data-child-collection="root"]').click();
    assert.equal(app.doc.querySelector('#collection-parent').value,'root');
    app.doc.querySelector('#new-collection-name').value='道家';
    app.doc.querySelector('#create-collection').click();
    await app.flush();
    assert.deepEqual(app.calls.find(c=>c.route.includes('/collection/create')).body,{name:'道家',parent_id:'root'});
  } finally {app.close();}
});

test('九级分类停用新增入口，父分类可切换仅本级资料', async () => {
  const cols=Array.from({length:9},(_,i)=>({...collection('level'+i,'第'+(i+1)+'级',1),
    parent_id:i?'level'+(i-1):null,depth:i+1,child_count:i<8?1:0,direct_count:i===8?1:0,
    path:Array.from({length:i+1},(_,n)=>'第'+(n+1)+'级'),path_label:'第'+(i+1)+'级'}));
  const app=await boot({collections:cols});
  try {
    assert.ok(app.doc.querySelector('[data-collection="level8"]').classList.contains('collection-depth-9'));
    assert.equal(app.doc.querySelector('[data-collection="level8"]').getAttribute('style'),null);
    app.doc.querySelector('[data-collection="level8"]').click();await app.flush();
    assert.equal(app.doc.querySelector('#new-subcollection').disabled,true);
    assert.equal(app.doc.querySelector('[data-child-collection="level8"]'),null);
    app.doc.querySelector('[data-collection="level0"]').click();await app.flush();
    const toggle=app.doc.querySelector('#include-child-collections');toggle.checked=false;
    toggle.dispatchEvent(new app.window.Event('change'));await app.flush();
    assert.match(app.calls.filter(c=>c.route.startsWith('/api/files?')).at(-1).route,/include_children=0/);
    assert.equal(app.doc.querySelector('#new-subcollection').disabled,false);
  } finally {app.close();}
});

test('移动分类排除自身后代并提交新的上级，删除说明整条分支', async () => {
  const cols=[
    {...collection('root','经学',1),depth:1,parent_id:null,child_count:1,path:['经学']},
    {...collection('child','易学',1),depth:2,parent_id:'root',child_count:0,path:['经学','易学']},
    {...collection('other','书籍',0),depth:1,parent_id:null,child_count:0,path:['书籍']}
  ];
  const app=await boot({collections:cols});
  try {
    app.doc.querySelector('[data-collection="root"]').click();await app.flush();
    app.doc.querySelector('#move-collection').click();await app.flush();
    const select=app.doc.querySelector('#move-collection-parent');
    assert.equal(select.querySelector('option[value="root"]'),null);
    assert.equal(select.querySelector('option[value="child"]'),null);
    select.value='other';app.doc.querySelector('#save-collection-move').click();await app.flush();
    assert.deepEqual(app.calls.find(c=>c.route.includes('/collection/move')).body,{collection_id:'root',parent_id:'other'});
    app.doc.querySelector('#delete-collection-button').click();await app.flush();
    assert.match(app.doc.querySelector('#confirm-detail').textContent,/1 个子分类/);
  } finally {app.close();}
});

test('知识筛选提交完整分类和精确标签，显示归属与可选位置',async()=>{
 const app=await boot({collections:[{...collection('c1','经典'),path_label:'经学 / 经典'}],responder:route=>{
  if(route==='/api/knowledge/profiles')return [{id:'default',name:'本库',enabled:true,all_files:true,source_ids:[],collection_ids:[]}];
  if(route.startsWith('/api/knowledge/info'))return {files:21,passages:21};
  if(route.startsWith('/api/knowledge/search'))return {results:[{id:'f1',title:'青山',snippet:'证据',collections:[{path_label:'经学 / 经典'}],origin_path:'/资料/<成品>.md',preferred_file:{title:'修订版',reason:'已校对'}}]};
 }});
 try{app.doc.querySelector('#knowledge-button').click();await app.flush();app.doc.querySelector('#knowledge-collection').value='c1';app.doc.querySelector('#knowledge-tags').value='状态:成品，载体:HTML';app.doc.querySelector('#knowledge-paths').checked=true;app.doc.querySelector('#knowledge-children').checked=false;app.doc.querySelector('#knowledge-query').value='青山';app.doc.querySelector('#knowledge-search-form').dispatchEvent(new app.window.Event('submit',{cancelable:true}));await app.flush();
 const call=app.calls.find(c=>c.route.startsWith('/api/knowledge/search')),params=new URL(call.route,'http://local').searchParams;assert.deepEqual(JSON.parse(params.get('collection_ids')),['c1']);assert.deepEqual(JSON.parse(params.get('tags')),['状态:成品','载体:HTML']);assert.equal(params.get('include_children'),'false');assert.equal(params.get('include_paths'),'true');assert.match(app.doc.querySelector('#knowledge-results').textContent,/经学 \/ 经典/);assert.match(app.doc.querySelector('#knowledge-results').textContent,/修订版/);assert.equal(app.doc.querySelector('#knowledge-results 成品'),null);
 }finally{app.close();}
});

test('分类规则保存不入库，规则编辑带版本，预览冲突阻止确认',async()=>{
 let saved=null;
 const app=await boot({collections:[{...collection('c1','主题'),path_label:'主题'}],responder:(route,body)=>{
  if(route==='/api/classification/rules')return {rules:saved?[saved]:[]};
  if(route==='/api/classification/batches')return {batches:[]};
  if(route==='/api/classification/rule'){saved={...body,id:'rule1',version:(saved?.version||0)+1,collection_path:['主题']};return saved;}
 }});
 try{app.doc.querySelector('#classification-rules-open').click();await app.flush();app.doc.querySelector('#rule-name').value='成品';app.doc.querySelector('#rule-path').value='/成品/';app.doc.querySelector('#rule-extensions').value='md';app.doc.querySelector('#rule-collection').value='c1';app.doc.querySelector('#rule-tags').value='状态:成品';app.doc.querySelector('#classification-rule-form').dispatchEvent(new app.window.Event('submit',{cancelable:true}));await app.flush();assert.equal(app.calls.some(c=>c.route==='/api/discovery/import'),false);assert.equal(saved.conditions.path_contains,'/成品/');assert.deepEqual(saved.conditions.extensions,['.md']);app.doc.querySelector('#rule-tags').value='状态:校对';app.doc.querySelector('#classification-rule-form').dispatchEvent(new app.window.Event('submit',{cancelable:true}));await app.flush();assert.equal(app.calls.filter(c=>c.route==='/api/classification/rule')[1].body.expected_version,1);
 }finally{app.close();}
});
