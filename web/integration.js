'use strict';
const $ = selector => document.querySelector(selector);
const app = $('meta[name="integration-app"]').content;
const token = app === 'vault' ? $('meta[name="vault-token"]').content : sessionStorage.getItem('xj-token');
const prefix = app === 'vault' ? '/api/agent/' : '/api/bridge/';
const header = app === 'vault' ? 'X-Vault-Token' : 'X-Console-Token';
const esc = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[character]));
const names = {queued:'排队中', submitted:'已派发', preparing:'准备中', running:'执行中', planning:'分工中', review:'待验收', accepted:'已验收', cancelled:'已取消', failed:'失败', timeout:'超时', interrupted:'已中断', partial:'部分完成', done:'完成', rejected:'已拒绝', handoff:'待交接', unpublished:'尚未入库', importing:'正在入库', published:'可检索', stored:'已保存', error:'入库未完成'};
const catalogState = {request:'', query:'', hidden:false, offset:0, next:null, total:0};
let items = [];
let selected = null;
let notificationRows = [];
let searchResults = [];
let references = [];
let versionsCache = new Map();
let requestKey = crypto.randomUUID();
let refreshSequence = 0;
let refreshPending = 0;
let searchSequence = 0;

function message(text, error = false) {
  $('#notice').textContent = text;
  $('#notice').className = error ? 'error' : '';
}

async function api(route, data, binary = false) {
  const response = await fetch(prefix + route, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {[header]:token, ...(data === undefined ? {} : {'Content-Type':'application/json'})},
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  if (!response.ok) {
    const result = await response.json();
    throw Error(result.error || '操作未完成');
  }
  return binary ? response.blob() : response.json();
}

function guard(action) {
  return async event => {
    try {await action(event);} catch (error) {message(error.message, true);}
  };
}

function size(bytes) {
  return bytes < 1024 ? bytes + ' B' : bytes < 1048576 ? (bytes/1024).toFixed(1) + ' KB' : (bytes/1048576).toFixed(1) + ' MB';
}

function catalogQuery(state = catalogState) {
  const query = new URLSearchParams({offset:state.offset, q:state.query, hidden:state.hidden ? '1' : '0'});
  if (state.request) query.set('request', state.request);
  return query;
}

function applyCatalogPage(catalog) {
  items = catalog.items;
  catalogState.total = catalog.total;
  catalogState.offset = catalog.offset;
  catalogState.next = catalog.next;
  renderCatalog();
  $('#page').textContent = (catalog.total ? catalog.offset + 1 : 0) + '–' + Math.min(catalog.offset + catalog.items.length, catalog.total) + ' / ' + catalog.total + ' 个登记版本';
  $('#prev').disabled = catalog.offset === 0;
  $('#next').disabled = catalog.next === null;
}

async function refresh({background = false} = {}) {
  if (background && refreshPending) return;
  const sequence = ++refreshSequence;
  const query = catalogQuery();
  refreshPending++;
  try {
    const [info, requests, catalog, notifications] = await Promise.all([
      api('info'), api('requests'), api('artifacts?' + query), api('notifications'),
    ]);
    if (sequence !== refreshSequence) return;
    if (catalog.total && catalog.offset >= catalog.total) {
      catalogState.offset = Math.floor((catalog.total - 1) / 50) * 50;
      await refresh();
      return;
    }
    renderNotifications(notifications.items);
    $('#connection').textContent = info.connected ? '● 协作台已连接' : '○ 协作台未连接，请打开桌面协作台';
    $('#connection').title = info.error || '';
    $('#sync').textContent = info.last_sync ? '目录更新于 ' + new Date(info.last_sync*1000).toLocaleTimeString() : '目录尚未同步';
    $('#stats').textContent = catalog.total + ' 个匹配的登记版本';
    renderRequests(requests.items);
    applyCatalogPage(catalog);
  } finally {
    refreshPending--;
  }
}

function renderRequests(rows) {
  $('#requests').innerHTML = rows.map(request => `<article class="request">
    <h3>${esc(request.title)}</h3><span class="badge">${esc(names[request.state] || request.state)}</span>
    <span class="meta"> ${esc(request.id)} · ${esc(request.members.length ? request.members.join(' + ') : request.worker)}</span>
    ${request.error ? `<p class="warn-text">${esc(request.error)}</p>` : ''}
    ${request.snapshot.progress ? `<p class="fine">${esc(request.snapshot.progress.label || request.snapshot.progress.message || '执行状态以协作台为准')}</p>` : ''}
    ${request.snapshot.tasks?.length ? `<p class="fine">${request.snapshot.tasks.map(task => esc(task.worker) + ' · ' + esc(names[task.state] || task.state)).join(' / ')}</p>` : ''}
    <div class="actions"><button data-request="${esc(request.id)}">查看成果</button>
    ${request.state === 'review' ? `<button data-accept="${esc(request.id)}">验收任务</button>` : ''}
    ${['queued', 'submitted', 'preparing', 'running', 'planning'].includes(request.state) ? `<button data-cancel="${esc(request.id)}">取消任务</button>` : ''}</div>
    ${request.snapshot.result ? `<details><summary>执行者回复</summary><p>${esc(request.snapshot.result)}</p></details>` : ''}
    </article>`).join('') || '<p class="empty">还没有跨应用请求。可以在左侧或 QQ 发起。</p>';
}

function contentGroups(rows) {
  const groups = new Map();
  for (const item of rows) {
    const key = item.task + ':' + item.sha256;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.values()].map(versions => {
    const sorted = [...versions].sort((left, right) => Number(Boolean(right.is_final)) - Number(Boolean(left.is_final)) || String(right.ended || right.at).localeCompare(String(left.ended || left.at)));
    return {item:sorted[0], inPage:versions.length};
  });
}

function renderCatalog() {
  const filter = catalogState.request ? `<div class="empty">正在查看请求 ${esc(catalogState.request)} <button id="clear-filter">查看全部请求</button></div>` : '';
  const groups = contentGroups(items);
  const cards = groups.map(({item, inPage}) => `<article class="card ${item.is_final ? 'final-card' : ''}">
    <div class="meta">${esc(item.worker)} / ${esc(item.request_id || '原有协作台任务')}</div>
    <h3>${esc(item.path.split('/').pop())}</h3><p>${esc(item.title)}</p>
    ${item.is_final ? '<span class="badge final-badge">最终稿 · 主交付物</span> ' : ''}
    <span class="badge ${item.task_state === 'accepted' ? '' : 'warn'}">${esc(names[item.task_state] || item.task_state)}</span>
    <span class="badge">${esc(names[item.publish_state] || item.publish_state)}</span>
    <p class="meta">${size(item.bytes)} · SHA ${esc(item.sha256.slice(0,12))} · ${esc(item.attempt)}</p>
    ${item.import_id ? `<p class="meta">${item.shared ? '本次加入小鲸成果分类' : '本次仅保存资料'}</p>` : ''}
    ${item.label ? `<p>${esc(item.label)}</p>` : ''}${item.error ? `<p class="warn-text">${esc(item.error)}</p>` : ''}
    ${item.content_version_count > 1 ? `<p class="fine">相同内容共 ${item.content_version_count} 个登记版本${inPage > 1 ? '，本页已合并 ' + inPage + ' 条' : ''}</p>` : ''}
    <div class="actions"><button data-artifact="${esc(item.id)}">查看与管理</button>
    <button data-preview="${esc(item.id)}" ${item.ended ? '' : 'disabled'}>预览文件</button>
    ${item.content_version_count > 1 ? `<button data-versions="${esc(item.id)}">相同内容版本</button>` : ''}
    ${item.file_id ? `<button data-read="${esc(item.file_id)}" data-sha="${esc(item.sha256)}">阅读入库文件</button>` : ''}</div>
    </article>`).join('');
  $('#catalog').innerHTML = filter + (cards || '<p class="empty">当前没有匹配的登记成果。请核对请求筛选、关键词和隐藏选项。</p>');
}

function findArtifact(identifier) {
  return items.find(item => item.id === identifier) || versionsCache.get(identifier);
}

function detail(identifier) {
  selected = findArtifact(identifier);
  if (!selected) return;
  $('#versions').close();
  $('#detail-title').textContent = selected.path.split('/').pop();
  $('#detail-info').textContent = `成果 ${selected.id}\n任务 ${selected.task}\n尝试 ${selected.attempt}\n原路径 ${selected.path}\nSHA-256 ${selected.sha256}\n${size(selected.bytes)} · ${names[selected.task_state] || selected.task_state}\n入库 ${names[selected.publish_state] || selected.publish_state}\n${selected.is_final ? '当前最终稿 · 主交付物' : '可在验收通过后指定为主交付物'}`;
  $('#label').value = selected.label;
  $('#note').value = selected.note;
  $('#hidden').checked = Boolean(selected.hidden);
  $('#publish-share').checked = Boolean(selected.shared);
  $('#publish').disabled = ['queued', 'importing', 'published'].includes(selected.publish_state) || !selected.ended;
  $('#copy-citation').disabled = !selected.file_id;
  $('#set-final').textContent = selected.is_final ? '取消最终稿标记' : '指定为最终稿';
  $('#set-final').disabled = !selected.is_final && (selected.task_state !== 'accepted' || !selected.ended);
  $('#preview-detail').disabled = !selected.ended;
  $('#publish-help').textContent = selected.task_state === 'accepted' ? '选定版本将按登记哈希托管复制。最终稿标记绑定此文件版本。' : '保留稿入库需要明确确认，最终稿在任务验收后指定。';
  $('#detail').showModal();
}

async function preview(item) {
  if (!item) return;
  message('正在核对文件版本…');
  const blob = await api('download?id=' + encodeURIComponent(item.id), undefined, true);
  await window.ArtifactPreview.open({name:item.path.split('/').pop(), mime:item.mime || '', blob, sha256:item.sha256});
  message('已打开校验版本');
}

async function showVersions(identifier) {
  const result = await api('artifact-versions?' + new URLSearchParams({id:identifier}));
  versionsCache = new Map(result.items.map(item => [item.id, item]));
  $('#version-list').innerHTML = result.items.map(item => `<article class="card">
    <h3>${esc(item.path)}</h3><p class="meta">${esc(item.attempt)} · ${esc(item.ended || item.at || '')}</p>
    ${item.is_final ? '<span class="badge final-badge">最终稿</span> ' : ''}${item.hidden ? '<span class="badge">已隐藏</span>' : ''}
    <div class="actions"><button data-artifact="${esc(item.id)}">查看与管理</button><button data-preview="${esc(item.id)}" ${item.ended ? '' : 'disabled'}>预览文件</button></div>
    </article>`).join('');
  $('#versions').showModal();
}

async function read(identifier, sha, extract) {
  const result = await api('read?' + new URLSearchParams({id:identifier, ...(sha ? {sha} : {}), ...(extract ? {extract} : {})}));
  $('#read-content').textContent = (result.title || result.name || identifier) + '\n\n' + (result.chunks || []).map(chunk => chunk.text || chunk.content || '').join('\n\n') + '\n\n' + (result.next_start != null ? '后续片段可在拾页原文阅读器查看。' : '');
  $('#reader').showModal();
}

function renderNotifications(rows) {
  notificationRows = rows;
  const labels = {pending:'等待发送', sending:'发送中', sent:'发送接口已确认', unknown:'投递结果未知', confirmed:'已人工确认收到', dismissed:'已忽略'};
  $('#notifications').innerHTML = rows.map(row => `<article class="card"><strong>${esc(row.request_id)}</strong><p>${esc(labels[row.state] || row.state)}</p><p class="meta">尝试 ${row.attempts} 次</p>
    ${row.state === 'unknown' ? `<div class="actions"><button data-notification="${esc(row.id)}" data-resolution="confirm_delivered">已收到，确认</button><button data-notification="${esc(row.id)}" data-resolution="retry">核对后补发</button><button data-notification="${esc(row.id)}" data-resolution="dismiss">忽略此通知</button></div>` : ''}</article>`).join('') || '<p class="empty">还没有 QQ 通知记录。</p>';
}

function referenceFromResult(result) {
  return {id:result.id || result.file_id, sha256:result.sha256, extraction_sha256:result.extraction_sha256 || '', title:result.title || result.name || result.id, citation:result.citation || 'shiyue://files/' + (result.id || result.file_id)};
}

function renderReferences() {
  $('#reference-count').textContent = references.length + ' / 8 份参考资料';
  $('#knowledge-reference-count').textContent = '已选 ' + references.length + ' 份';
  $('#reference-list').innerHTML = references.map(reference => `<li><div><strong>${esc(reference.title)}</strong><p class="meta">${esc(reference.citation)}<br>SHA ${esc(reference.sha256.slice(0,12))}${reference.extraction_sha256 ? ' · 提取版本 ' + esc(reference.extraction_sha256.slice(0,12)) : ''}</p></div><button type="button" data-remove-reference="${esc(reference.id)}" aria-label="移除参考资料 ${esc(reference.title)}">移除</button></li>`).join('') || '<li class="fine">在下方知识检索中勾选资料，派发时会核对授权范围及文件版本。</li>';
}

function renderSearchResults() {
  $('#knowledge-results').innerHTML = searchResults.map(result => {
    const reference = referenceFromResult(result);
    const valid = /^[0-9a-f]{32}$/.test(reference.id || '') && /^[0-9a-f]{64}$/.test(reference.sha256 || '');
    return `<article class="card"><h3>${esc(reference.title)}</h3><p>${esc(result.snippet || '')}</p><p class="meta">${esc(reference.citation)}</p>
      <div class="actions"><button data-read="${esc(reference.id)}" data-sha="${esc(reference.sha256)}" data-extract="${esc(reference.extraction_sha256)}">阅读原文</button>
      <label class="reference-choice"><input type="checkbox" data-reference="${esc(reference.id)}" ${references.some(item => item.id === reference.id) ? 'checked' : ''} ${valid ? '' : 'disabled'}> 作为任务参考</label></div>
      ${valid ? '' : '<p class="fine">版本信息不完整，请在拾页核对后再选作参考。</p>'}</article>`;
  }).join('') || '<p class="empty">所选范围内没有匹配结果。</p>';
}

async function loadScopes() {
  try {
    const result = await api('scope');
    $('#scope').innerHTML = result.profiles.map(profile => `<option value="${esc(profile.id)}" ${profile.id === result.profile ? 'selected' : ''} ${profile.enabled ? '' : 'disabled'}>${esc(profile.name)}${profile.all_files ? ' · 全库' : ''}</option>`).join('');
  } catch {
    $('#scope').innerHTML = '<option value="">拾页尚未连接</option>';
  }
}

$('#refresh').onclick = guard(() => refresh());
$('#footer-app').textContent = app === 'vault' ? '拾页 · Agent 成果' : '小鲸控制台 · 三端协作';
$('#dispatch').onsubmit = guard(async event => {
  event.preventDefault();
  const members = [...document.querySelectorAll('[name=member]:checked')].map(input => input.value);
  if (members.length === 1) throw Error('团队请至少选择两位，单人任务使用上方执行者');
  const button = event.target.querySelector('button[type=submit]');
  button.disabled = true;
  try {
    const result = await api('requests', {prompt:$('#prompt').value, worker:$('#worker').value, members, request_key:requestKey,
      references:references.map(reference => ({id:reference.id, sha256:reference.sha256, ...(reference.extraction_sha256 ? {extraction_sha256:reference.extraction_sha256} : {})}))});
    requestKey = crypto.randomUUID();
    message('已登记 ' + result.id + '，参考资料将随任务保留。');
    await refresh();
  } finally {
    button.disabled = false;
  }
});

$('#filter').onsubmit = guard(async event => {
  event.preventDefault();
  catalogState.offset = 0;
  catalogState.query = $('#query').value;
  catalogState.hidden = $('#show-hidden').checked;
  await refresh();
});
$('#prev').onclick = guard(async () => {catalogState.offset = Math.max(0, catalogState.offset - 50); await refresh();});
$('#next').onclick = guard(async () => {catalogState.offset = catalogState.next ?? catalogState.offset; await refresh();});
$('#close-detail').onclick = () => $('#detail').close();
$('#close-reader').onclick = () => $('#reader').close();
$('#close-versions').onclick = () => $('#versions').close();
$('#preview-detail').onclick = guard(() => preview(selected));
$('#save-meta').onclick = guard(async () => {
  await api('metadata', {id:selected.id, label:$('#label').value, note:$('#note').value, hidden:$('#hidden').checked});
  message('管理信息已保存');
  $('#detail').close();
  await refresh();
});
$('#set-final').onclick = guard(async event => {
  if (!selected) return;
  const action = selected.is_final ? 'clear' : 'set';
  if (!confirm(action === 'clear' ? '确认取消此版本的最终稿标记？' : '确认将此精确版本设为该任务的主交付物？')) return;
  const button = event.target;
  button.disabled = true;
  try {
    await api('artifact-final', {id:selected.id, sha256:selected.sha256, action, expected_id:selected.final_artifact_id || '', confirmed:true});
    message(action === 'clear' ? '已取消最终稿标记' : '已保存最终稿选择');
    $('#detail').close();
    await refresh();
  } finally {button.disabled = false;}
});
$('#download').onclick = guard(async () => {
  const blob = await api('download?id=' + encodeURIComponent(selected.id), undefined, true);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = selected.path.split('/').pop();
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  message('已校验登记哈希并下载');
});
$('#publish').onclick = guard(async () => {
  const draft = selected.task_state !== 'accepted';
  if (!confirm(`${draft ? '这是尚未验收的保留稿。\n' : ''}将此版本托管复制到拾页${$('#publish-share').checked ? '并加入小鲸成果分类' : ''}？\n${selected.path}\nSHA ${selected.sha256}`)) return;
  await api('publish', {id:selected.id, sha256:selected.sha256, confirmed:true, allow_draft:draft, share:$('#publish-share').checked});
  message('已登记入库请求');
  $('#detail').close();
  await refresh();
});
$('#copy-citation').onclick = guard(async () => {
  await navigator.clipboard.writeText('shiyue://files/' + selected.file_id + '?sha=' + selected.sha256);
  message('已复制拾页本机引用');
});
$('#save-scope').onclick = guard(async () => {
  if (!$('#scope').value) throw Error('请先打开拾页');
  if (!confirm('将小鲸的检索范围切换到所选范围？')) return;
  await api('scope', {profile:$('#scope').value});
  searchSequence++;
  searchResults = [];
  renderSearchResults();
  message('检索范围已更新，派发时会重新核对参考资料授权');
});
$('#search-form').onsubmit = guard(async event => {
  event.preventDefault();
  const sequence = ++searchSequence;
  const result = await api('knowledge?' + new URLSearchParams({q:$('#knowledge-query').value}));
  if (sequence !== searchSequence) return;
  searchResults = result.results || [];
  renderSearchResults();
});

document.addEventListener('change', guard(async event => {
  const input = event.target.closest('[data-reference]');
  if (!input) return;
  if (!input.checked) references = references.filter(reference => reference.id !== input.dataset.reference);
  else if (references.length >= 8) {input.checked = false; throw Error('每次任务最多选择 8 份参考资料');}
  else {
    const result = searchResults.find(result => (result.id || result.file_id) === input.dataset.reference);
    if (result && !references.some(reference => reference.id === input.dataset.reference)) references.push(referenceFromResult(result));
  }
  renderReferences();
}));

document.addEventListener('click', guard(async event => {
  const button = event.target.closest('button');
  if (!button) return;
  if (button.dataset.artifact) detail(button.dataset.artifact);
  if (button.dataset.preview) await preview(findArtifact(button.dataset.preview));
  if (button.dataset.versions) await showVersions(button.dataset.versions);
  if (button.dataset.read) await read(button.dataset.read, button.dataset.sha, button.dataset.extract);
  if (button.dataset.request) {
    catalogState.request = button.dataset.request;
    catalogState.offset = 0;
    await refresh();
    $('#artifacts').scrollIntoView();
  }
  if (button.id === 'clear-filter') {catalogState.request = ''; catalogState.offset = 0; await refresh();}
  if (button.dataset.removeReference) {
    references = references.filter(reference => reference.id !== button.dataset.removeReference);
    renderReferences();
    renderSearchResults();
  }
  for (const action of ['accept', 'cancel']) {
    if (button.dataset[action] && confirm(action === 'accept' ? '确认已检查该任务交付并通过验收？' : '确认取消该请求及尚在执行的成员任务？')) {
      await api(action, {id:button.dataset[action], confirmed:true});
      message('操作已提交');
      await refresh();
    }
  }
  if (!button.dataset.notification) return;
  const notification = notificationRows.find(row => row.id === button.dataset.notification);
  if (!notification) return;
  const action = button.dataset.resolution;
  const question = action === 'retry' ? '请先核对 QQ 聊天记录。确认需要再发送一次这条通知？' : action === 'confirm_delivered' ? '确认已在 QQ 收到这条通知？' : '确认忽略这条通知？';
  if (!confirm(question)) return;
  button.disabled = true;
  try {
    await api('notifications', {id:notification.id, revision:notification.revision, action, confirmed:true});
    message('通知处理结果已保存');
    await refresh();
  } finally {button.disabled = false;}
}));

window.addEventListener('native-citation', guard(async event => {
  const citation = new URL(event.detail.url);
  if (citation.protocol !== 'shiyue:' || citation.hostname !== 'files') return;
  const identifier = citation.pathname.split('/')[1];
  if (!/^[0-9a-f]{32}$/.test(identifier)) return;
  await read(identifier, citation.searchParams.get('sha'), citation.searchParams.get('extract'));
}));

window.LocalCollaboration = {api, message, refresh};
renderReferences();
guard(() => refresh())();
loadScopes();
setInterval(() => {
  if (!document.hidden && !['#detail', '#reader', '#versions', '.artifact-preview-dialog'].some(selector => $(selector)?.open)) {
    guard(() => refresh({background:true}))();
  }
}, 8000);
