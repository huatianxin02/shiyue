'use strict';
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const token=$('meta[name="vault-token"]').content;
const state={view:'all',source:'',collection:'',includeChildren:true,tag:'',after:'',before:'',q:'',offset:0,files:[],total:0,selected:new Set(),status:null,detail:null,seq:0,busy:false,layout:'list',sort:'added',camera:'',month:''};
const names={all:'全部资料',document:'文档',image:'图片',video:'视频',starred:'我的收藏'};
const jobNames={rebuild_knowledge:'重建检索索引',repository_backup:'增量备份',backup:'备份资料库',ocr:'本地文字识别',import:'导入与索引',export:'导出完整资料包',mount:'校验并挂载资料包',verify:'检查内容完整性',refresh:'更新预览与索引'};
const jobStates={queued:'排队中',running:'进行中',done:'已完成',failed:'失败',partial:'部分完成',cancelled:'已取消'};
const iconPaths={
 library:'M4 4h6v16H4z M14 4h6v16h-6z M7 8h0 M17 8h0',
 document:'M14 3H5v18h14V8z M14 3v5h5 M8 12h8 M8 16h6',
 image:'M3 4h18v16H3z M3 16l5-5 5 5 3-3 5 5 M16 8h0',
 video:'M3 6h12v12H3z M15 10l6-4v12l-6-4',
 star:'m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z',
 tasks:'M21 12a9 9 0 1 1-9-9 M12 7v5l3 2 M16 4l2 2 4-4',
 storage:'M4 7h16v14H4z M3 3h18v4H3z M9 11h6',
 settings:'M9 2h6v3l2 1 2-1 3 5-2 2 2 2-3 5-2-1-2 1v3H9v-3l-2-1-2 1-3-5 2-2-2-2 3-5 2 1 2-1z M15.5 12a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0',
 health:'M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6z m-4 9 3 3 5-6',
 trash:'M3 6h18 M9 6V3h6v3 M5 6l1 15h12l1-15 M10 10v7 M14 10v7',
 collaboration:'M4 7h15l-4-4 M20 17H5l4 4 M19 7l-4 4 M5 17l4-4',
 plus:'M12 5v14 M5 12h14',search:'M20 20l-5-5 M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
 refresh:'M20 7a9 9 0 1 0 1 7 M20 2v5h-5',
 filter:'M4 6h16 M7 12h10 M10 18h4',list:'M8 6h13 M8 12h13 M8 18h13 M3 6h0 M3 12h0 M3 18h0',
 grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
 chevron:'m6 9 6 6 6-6',folder:'M3 6h7l2 3h9v11H3z'
};
function icon(name){return `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${iconPaths[name]||iconPaths.document}"></path></svg>`;}
$$('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
$$('.chevron').forEach(el=>el.innerHTML=icon('chevron'));
let filtersExpanded=false,detailFocus=null;
function syncFilters(){
 const mediaView=['image','video'].includes(state.view);
 const hasMedia=mediaView||Boolean(state.status?.stats.counts.image||state.status?.stats.counts.video);
 $('#filter-toggle').hidden=!hasMedia;
 const expanded=hasMedia&&filtersExpanded;
 $('#media-filters').hidden=!expanded;
 $('#filter-toggle').setAttribute('aria-expanded',String(expanded));
 const count=Number(Boolean(state.camera))+Number(Boolean(state.month));
 $('#filter-label').textContent=count?`筛选 · ${count}`:'筛选';
 $('#filter-toggle').setAttribute('aria-label',count?`拍摄筛选，已启用 ${count} 个条件`:'拍摄筛选');
 $('#filter-toggle').classList.toggle('has-filters',count>0);
}
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function size(n){if(!n)return '0 B';const units=['B','KB','MB','GB','TB'];const k=Math.min(Math.floor(Math.log(n)/Math.log(1024)),4);return `${(n/1024**k).toFixed(k?1:0)} ${units[k]}`;}
function date(s){return s?new Date(s).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'尚未扫描';}
function highlight(text,q=state.q){if(!q.trim())return esc(text);const parts=text.split(new RegExp('('+q.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','gi'));return parts.map(p=>p.toLowerCase()===q.toLowerCase()?`<mark>${esc(p)}</mark>`:esc(p)).join('');}
function toast(message,error=false){
 const dialog=document.activeElement?.closest('dialog[open]')||$$('dialog[open]').at(-1);
 let el=$('#toast');
 if(dialog){el=dialog.querySelector('.inline-feedback');if(!el){el=document.createElement('p');el.className='inline-feedback';el.setAttribute('role','status');dialog.append(el);}}
 el.textContent=message;el.classList.toggle('error',error);el.hidden=false;clearTimeout(el._timer);el._timer=setTimeout(()=>el.hidden=true,4500);
}
async function api(path,body){const res=await fetch('/api'+path,{method:body===undefined?'GET':'POST',headers:{'X-Vault-Token':token,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const data=await res.json();if(!res.ok)throw Error(data.error||'操作未完成');return data;}
async function action(fn){try{return await fn();}catch(e){toast(e.message,true);}}
function showDialog(id){const dlg=$(id);if(id!=='#settings-dialog'&&$('#settings-dialog').open)$('#settings-dialog').close();if(!dlg.open)dlg.showModal();}
function showSettings(){for(const dialog of $$('dialog[open]'))dialog.close();showDialog('#settings-dialog');}
// Sidebar groups fold independently and remember the choice: a source list with
// thirty folders should not push the collections out of reach.
const folds={sources:folded('sources'),collections:folded('collections')};
function folded(key){if(typeof window.shiyuePreferences?.[key]==='boolean')return window.shiyuePreferences[key];try{return localStorage.getItem('shiyue.fold.'+key)==='1'}catch(e){return false}}
function applyFold(key){const list=$(key==='sources'?'#sources':'#collections'),btn=$(`[data-fold="${key}"]`);if(list)list.hidden=folds[key];if(btn)btn.setAttribute('aria-expanded',String(!folds[key]));}
function toggleFold(key){folds[key]=!folds[key];try{localStorage.setItem('shiyue.fold.'+key,folds[key]?'1':'0')}catch(e){}window.webkit?.messageHandlers?.native?.postMessage({action:'preference',key,folded:folds[key]});applyFold(key);}
// WKWebView has no UIDelegate here, so window.confirm() silently returns false and
// every destructive action would look like it was declined. Ask in the page instead.
let confirmResolve=null;
function confirmAction({title,message,details=[],ok='确认'}){
  return new Promise(resolve=>{
    confirmResolve=resolve;
    $('#confirm-title').textContent=title;
    $('#confirm-message').textContent=message;
    $('#confirm-detail').innerHTML=details.map(d=>`<li>${esc(d)}</li>`).join('');
    $('#confirm-ok').textContent=ok;
    showDialog('#confirm-dialog');
  });
}
function settleConfirm(value){
  const resolve=confirmResolve;
  confirmResolve=null;
  $('#confirm-dialog').close();
  if(resolve)resolve(value);
}
async function disconnectSource(sid){
  const impact=await api('/source/impact',{source_id:sid});
  const details=[
    `「${impact.source.name}」的 ${impact.files} 份资料索引会移入回收站。`,
    impact.bytes?`回收站将保留托管副本与预览，占用 ${size(impact.bytes)}。`:'这个来源没有托管副本，不占用资料库空间。',
    impact.shared?`其中 ${impact.shared} 个对象仍被其他来源使用，会保留。`:'',
    '记录与托管副本进入回收站，可恢复；原始位置的文件不会被删除或移动。'
  ].filter(Boolean);
  const ok=await confirmAction({title:'断开这个来源？',message:impact.source.root,details,ok:'断开来源'});
  if(!ok)return;
  await api('/source/remove',{source_id:sid});
  toast('来源已移入回收站');
  if(state.source===sid)await setView('all','');
  else{await refreshStatus();await loadFiles();}
}
async function removeFiles(ids,label=''){
  const impact=await api('/file/impact',{ids});
  const details=[impact.files===1&&label?`「${label}」的记录、标签与原文片段会移入回收站。`:`${impact.files} 份资料的记录、标签与原文片段会移入回收站。`];
  if(impact.bytes)details.push(`回收站将保留托管副本，占用 ${size(impact.bytes)}。`);
  if(impact.shared)details.push(`其中 ${impact.shared} 个对象仍被其他资料使用，会保留。`);
  if(impact.unique)details.push(`注意：${impact.unique} 份资料的原件已不在原位置，资料库中的副本是唯一一份，移入回收站后仍可恢复；永久清理前请先备份。`);
  if(impact.offline)details.push(`${impact.offline} 份资料的原件已不在，移除只是清掉这条记录。`);
  details.push('原始位置的文件不会被删除或移动。');
  const ok=await confirmAction({
    title:impact.files===1?'从资料库移除这份资料？':`从资料库移除 ${impact.files} 份资料？`,
    message:label||'',
    details,ok:'移除'
  });
  if(!ok)return;
  const result=await api('/file/remove',{ids});
  closeDetail();
  state.selected.clear();
  toast(`已将 ${result.files} 份资料移入回收站`);
  await refreshStatus();await loadFiles();
}
// Collection IDs stay stable when a branch is renamed or moved.
let collectionTarget=[],collectionPresent=[];
let collectionDragId=null,collectionMovePending=false,collectionDragOver=null,collectionScrollTimer=null;
const collectionDragType='application/x-shiyue-collection';
let collapsedCollections=new Set();
try{collapsedCollections=new Set(JSON.parse(localStorage.getItem('shiyue.collection.collapsed')||'[]'));}catch(e){}
function collectionLabel(collection){return collection.path_label||collection.name;}
function collectionName(id){const collection=state.status?.collections?.find(c=>c.id===id);return collection?collectionLabel(collection):'';}
function renderCollectionTree(){
  // Polling must not detach the native drag source during a drag session.
  if(collectionDragId)return;
  const cols=state.status?.collections||[];
  const byId=new Map(cols.map(c=>[c.id,c]));
  const visible=cols.filter(c=>{
    let parent=c.parent_id;
    while(parent){if(collapsedCollections.has(parent))return false;parent=byId.get(parent)?.parent_id;}
    return true;
  });
  $('#collections').innerHTML=visible.length?visible.map(c=>{
    const hasChildren=Boolean(c.child_count),expanded=!collapsedCollections.has(c.id);
    return `<div class="source-item collection-item collection-depth-${c.depth||1} ${state.collection===c.id?'active':''}" data-collection="${c.id}" draggable="true" role="treeitem" tabindex="0" aria-level="${c.depth||1}" aria-describedby="collection-drag-help" ${hasChildren?`aria-expanded="${expanded}"`:''} aria-selected="${state.collection===c.id}" title="${esc(collectionLabel(c))} · 拖动边缘排序，拖到中间移入子分类">${hasChildren?`<button class="collection-expand" data-toggle-collection="${c.id}" tabindex="-1" aria-label="${expanded?'收起':'展开'} ${esc(c.name)}">${expanded?'⌄':'›'}</button>`:'<span class="collection-expand-spacer"></span>'}<span class="collection-mark">${icon('folder')}</span><span class="name">${esc(c.name)}</span><b>${c.count}</b>${(c.depth||1)<9?`<button class="collection-child-add" data-child-collection="${c.id}" title="新建子分类" aria-label="在 ${esc(collectionLabel(c))} 下新建子分类">＋</button>`:''}</div>`;
  }).join(''):'<p class="sidebar-hint">新建分类，按主题整理资料</p>';
  $('#collection-total').textContent=cols.length;
}
function toggleCollection(cid){
  if(collapsedCollections.has(cid))collapsedCollections.delete(cid);else collapsedCollections.add(cid);
  try{localStorage.setItem('shiyue.collection.collapsed',JSON.stringify([...collapsedCollections]));}catch(e){}
  renderCollectionTree();
  $(`#collections [data-collection="${cid}"]`)?.focus();
}
function revealCollection(cid){
  const cols=state.status?.collections||[];
  let node=cols.find(c=>c.id===cid);
  while(node?.parent_id){collapsedCollections.delete(node.parent_id);node=cols.find(c=>c.id===node.parent_id);}
}
function renderCollectionChoices(){
  const cols=state.status?.collections||[];
  $('#collection-choices').innerHTML=cols.length?cols.map(c=>`<button class="collection-choice" data-pick-collection="${c.id}"><span>${esc(collectionLabel(c))}</span>${collectionPresent.includes(c.id)?'<i class="collection-check">已加入</i>':''}<b>${c.count}</b></button>`).join(''):'<p class="muted collection-empty">还没有分类。在下面输入名称，新建第一个。</p>';
}
function collectionDescendants(cid){
  const ids=new Set([cid]);
  for(const collection of state.status?.collections||[]){
    if(ids.has(collection.parent_id))ids.add(collection.id);
  }
  return ids;
}
function clearCollectionDrop(){
  $$('.collection-drop-before,.collection-drop-after,.collection-drop-inside,.collection-drop-invalid').forEach(el=>el.classList.remove('collection-drop-before','collection-drop-after','collection-drop-inside','collection-drop-invalid'));
  collectionDragOver=null;
  clearInterval(collectionScrollTimer);collectionScrollTimer=null;
}
function endCollectionDrag(){
  collectionDragId=null;clearCollectionDrop();
  $$('.collection-drag-source').forEach(el=>el.classList.remove('collection-drag-source'));
  $('#collection-root-drop').hidden=true;
  $('#collection-drag-status').textContent='';
}
function collectionDropPlan(cid,targetId,position){
  const cols=state.status?.collections||[],current=cols.find(c=>c.id===cid),target=cols.find(c=>c.id===targetId);
  if(!current||targetId&&!target)return {error:'分类已变化，请刷新后重试'};
  const branch=collectionDescendants(cid);
  if(branch.has(targetId))return {error:'不能拖到自身或子分类上'};
  const parentId=target?(position==='inside'?target.id:target.parent_id||null):null;
  const parent=cols.find(c=>c.id===parentId);
  const height=Math.max(...cols.filter(c=>branch.has(c.id)).map(c=>c.depth||1))-(current.depth||1)+1;
  if((parent?.depth||0)+height>9)return {error:'移动后将超过 9 级'};
  const asciiFold=name=>name.replace(/[A-Z]/g,char=>char.toLowerCase());
  if(cols.some(c=>c.id!==cid&&(c.parent_id||null)===parentId&&asciiFold(c.name)===asciiFold(current.name)))return {error:'目标上级下已有同名分类'};
  const payload=target?{collection_id:cid,target_id:target.id,position}:{collection_id:cid,parent_id:null};
  const message=target?(position==='inside'?`移入「${collectionLabel(target)}」成为子分类`:`放到「${target.name}」${position==='before'?'上方':'下方'}，与它同级`):'移到顶级分类末尾';
  return {payload,message,parentId};
}
async function applyCollectionDrop(plan){
  if(collectionMovePending)return;
  if(plan.error){toast(plan.error,true);return;}
  collectionMovePending=true;
  try{
    await api('/collection/move',plan.payload);
    await refreshStatus();revealCollection(plan.payload.collection_id);renderCollectionTree();
    await loadFiles();
    $(`#collections [data-collection="${plan.payload.collection_id}"]`)?.focus();
    toast('已保存分类位置');
  }catch(e){
    toast(e.message,true);
    try{await refreshStatus();}catch(ignored){}
  }finally{collectionMovePending=false;}
}
function handleCollectionDragOver(e){
  if(!collectionDragId||collectionMovePending)return;
  const row=e.target.closest('[data-collection]'),root=e.target.closest('#collection-root-drop');
  if(!row&&!root){clearCollectionDrop();return;}
  e.preventDefault();e.stopPropagation();
  const rect=(row||root).getBoundingClientRect();
  const fraction=rect.height?(e.clientY-rect.top)/rect.height:.5;
  const position=root?'inside':fraction<.25?'before':fraction>.75?'after':'inside';
  const targetId=row?.dataset.collection||null;
  const plan=collectionDropPlan(collectionDragId,targetId,position);
  clearCollectionDrop();
  collectionDragOver={element:row||root,targetId,position};
  (row||root).classList.add(plan.error?'collection-drop-invalid':`collection-drop-${position}`);
  $('#collection-drag-status').textContent=plan.error||plan.message;
  if(e.dataTransfer)e.dataTransfer.dropEffect=plan.error?'none':'move';
  const scroll=$('.sidebar-scroll'),bounds=scroll.getBoundingClientRect();
  const direction=e.clientY<bounds.top+28?-1:e.clientY>bounds.bottom-28?1:0;
  if(direction)collectionScrollTimer=setInterval(()=>{scroll.scrollTop+=direction*10;},30);
}
$('#collections').addEventListener('dragstart',e=>{
  const row=e.target.closest('[data-collection]');
  if(!row)return;
  if(collectionMovePending||e.target.closest('button')||!e.dataTransfer){e.preventDefault();return;}
  collectionDragId=row.dataset.collection;
  e.dataTransfer.setData(collectionDragType,collectionDragId);
  e.dataTransfer.effectAllowed='move';
  row.classList.add('collection-drag-source');
  $('#collection-root-drop').hidden=false;
  $('#collection-drag-status').textContent='边缘排序 · 中间移入 · Esc 取消';
  e.stopPropagation();
});
$('.sidebar-scroll').addEventListener('dragover',handleCollectionDragOver);
// Accept newly entered child elements too, so releasing just after crossing a
// label edge works before the browser emits its next periodic dragover event.
$('.sidebar-scroll').addEventListener('dragenter',handleCollectionDragOver);
$('.sidebar-scroll').addEventListener('dragleave',e=>{
  if(collectionDragId&&!e.currentTarget.contains(e.relatedTarget)){clearCollectionDrop();$('#collection-drag-status').textContent='边缘排序 · 中间移入 · Esc 取消';}
});
$('.sidebar-scroll').addEventListener('drop',e=>{
  if(!collectionDragId)return;
  e.preventDefault();e.stopPropagation();
  const target=collectionDragOver,cid=collectionDragId;
  const valid=target&&target.element.contains(e.target)&&e.dataTransfer?.getData(collectionDragType)===cid;
  endCollectionDrag();
  if(valid)applyCollectionDrop(collectionDropPlan(cid,target.targetId,target.position));
});
document.addEventListener('dragend',()=>{if(collectionDragId){endCollectionDrag();renderCollectionTree();}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&collectionDragId){endCollectionDrag();renderCollectionTree();}});
window.addEventListener('blur',()=>{if(collectionDragId){endCollectionDrag();renderCollectionTree();}});
function parentOptions(select,current=null,excluded=new Set()){
  select.innerHTML='<option value="">顶级分类</option>'+(state.status?.collections||[]).filter(c=>(c.depth||1)<9&&!excluded.has(c.id)).map(c=>`<option value="${c.id}">${esc(collectionLabel(c))}</option>`).join('');
  select.value=current||'';
}
function openCollectionPicker(ids,present=[],parentId=null){
  collectionTarget=ids;collectionPresent=present;
  $('#collection-title').textContent=ids.length>1?`把 ${ids.length} 份资料加入分类`:ids.length?'加入分类':parentId?'新建子分类':'新建分类';
  $('#collection-note').textContent=ids.length?'选择分类后即可加入，同一份资料可以归入多个分类。':'分类最多 9 级，可在下方选择上级分类。';
  $('#collection-error').textContent='';
  $('#new-collection-name').value='';
  parentOptions($('#collection-parent'),parentId);
  renderCollectionChoices();
  $('#collection-choices').hidden=!ids.length;
  showDialog('#collection-dialog');
  $('#new-collection-name').focus();
}
async function assignToCollection(payload){
  const result=await api('/collection/assign',{ids:collectionTarget,...payload});
  $('#collection-dialog').close();
  toast(result.action==='add'?`已加入「${result.collection.name}」，共 ${result.collection.count} 份资料`:`已从「${result.collection.name}」移除`);
  await refreshStatus();await loadFiles();
  if(state.detail)await openDetail(state.detail);
}
async function createCollectionFromDialog(){
  const input=$('#new-collection-name'),name=input.value.trim();
  if(!name){$('#collection-error').textContent='请输入分类名称';input.focus();return;}
  const parent_id=$('#collection-parent').value;
  const payload=parent_id?{name,parent_id}:{name};
  if(collectionTarget.length){await assignToCollection(payload);return;}
  const created=await api('/collection/create',payload);
  $('#collection-dialog').close();
  toast(`已新建分类「${created.name}」`);
  await refreshStatus();
  revealCollection(created.id);
  await setView('collection','',created.id);
}
async function leaveCollection(cid,ids){
  const result=await api('/collection/assign',{collection_id:cid,ids,action:'remove'});
  toast(`已从「${result.collection.name}」移除`);
  await refreshStatus();await loadFiles();
  if(state.detail)await openDetail(state.detail);
}
async function renameCollection(){
  const cid=state.collection,c=state.status?.collections?.find(x=>x.id===cid);
  if(!c)return;
  $('#rename-input').value=c.name;
  $('#rename-error').textContent='';
  showDialog('#rename-dialog');
  $('#rename-input').focus();$('#rename-input').select();
}
async function deleteCollection(){
  const cid=state.collection,c=state.status?.collections?.find(x=>x.id===cid);
  if(!c)return;
  const ok=await confirmAction({
    title:`删除分类「${c.name}」？`,
    message:`「${collectionLabel(c)}」汇总 ${c.count} 份资料。`,
    details:[`将删除此分类及其下的 ${collectionDescendants(c.id).size-1} 个子分类。`,'资料、标签与原件继续保留，之后可以重新归类。'],
    ok:'删除分类'
  });
  if(!ok)return;
  await api('/collection/delete',{collection_id:cid});
  toast(`已删除分类「${c.name}」`);
  await setView('all','');
}
async function deleteJob(id){
  const job=state.status?.jobs.find(j=>j.id===id);
  const running=!!job&&['queued','running'].includes(job.state);
  const ok=await confirmAction({
    title:running?'取消并删除这个任务？':'删除这条任务记录？',
    message:`${jobNames[job?.kind]||'任务'}${job?` · ${jobStates[job.state]||job.state}`:''}`,
    details:running?['任务会在下一个检查点停止，已完成的进度不会回滚。','已经导入的资料会留在资料库；如需清除，请断开对应来源。']:['只从任务列表移除这条记录。','已经导入的资料会留在资料库；如需清除，请断开对应来源。'],
    ok:'删除'
  });
  if(!ok)return;
  await api('/job/delete',{job_id:id});
  toast('任务已删除');
  await refreshStatus();
}
function showImport(){$('#import-error').textContent='';showDialog('#import-dialog');}
function showMount(){$('#storage-dialog').close();$('#mount-error').textContent='';showDialog('#mount-dialog');}
function mediaURL(f,route='thumbnail'){return `/api/files/${f.id}/${route}?v=${f.sha}`;}
function duration(n){if(n==null)return '';const secs=Math.round(n);return `${Math.floor(secs/60)}:${String(secs%60).padStart(2,'0')}`;}
function captureDate(value){return value?value.slice(0,19).replace('T',' '):'日期未记录';}
function fileIcon(f){const ext=f.name.includes('.')?f.name.split('.').pop().toUpperCase().slice(0,5):'FILE';return `<span class="file-icon ${f.kind==='image'?'image':f.kind==='video'?'video':ext==='PDF'?'pdf':''}">${esc(ext)}</span>`;}
function statusBadge(f){if(!f.available)return '<span class="status offline">原件离线</span>';if(f.integrity==='changed')return '<span class="status warning">内容有变化</span>';if(f.media_error)return `<span class="status warning" title="${esc(f.media_error)}">预览受限</span>`;if(f.warning)return `<span class="status warning" title="${esc(f.warning)}">仅部分索引</span>`;return '<span class="status">已索引</span>';}
async function refreshStatus(){
 const data=await api('/status');const before=state.status;state.status=data;
 $('#stat-files').innerHTML=`${data.stats.files.toLocaleString()}<small>份</small>`;
 const bytes=size(data.stats.bytes).split(' ');$('#stat-bytes').innerHTML=`${bytes[0]}<small>${bytes[1]}</small>`;
 $('#stat-sources').innerHTML=`${data.sources.length}<small>个</small>`;$('#source-total').textContent=data.sources.length;
 $('#count-all').textContent=data.stats.files;['document','image','video'].forEach(k=>$('#count-'+k).textContent=data.stats.counts[k]||0);$('#count-starred').textContent=data.stats.starred;
 const active=data.jobs.filter(j=>['queued','running'].includes(j.state));$('#job-count').textContent=active.length;$('#job-count').hidden=!active.length;
 $('#settings-version').textContent=data.version?`v${data.version}`:'';
 $('#settings-job-count').textContent=active.length;$('#settings-job-count').hidden=!active.length;
 $('#settings-button').title=active.length?`设置 · ${active.length} 项任务处理中`:'设置';
 $('#settings-button').setAttribute('aria-label',$('#settings-button').title);
 $('#footer-status').textContent=data.paused?'任务已暂停':active.length?`${active.length} 项任务处理中`:'索引就绪';
 $('#sources').innerHTML=data.sources.length?data.sources.map(s=>`<div class="source-item ${state.source===s.id?'active':''}" data-source="${s.id}" role="button" tabindex="0" title="${esc(s.root)}"><span class="dot ${s.online?'':'offline'}"></span><span class="name">${esc(s.name)}</span><b>${s.count}</b><button class="source-remove" data-remove-source="${s.id}" title="断开这个来源" aria-label="断开来源 ${esc(s.name)}">×</button></div>`).join(''):'<p class="sidebar-hint">添加文件夹或挂载资料包<br>开始建立你的资料库</p>';
 renderCollectionTree();
 updateFilterOptions(data);syncFilters();$('#data-path').textContent=data.data_path;$('#pause-button').textContent=data.paused?'继续任务':'暂停任务';renderJobs();renderSourceTools();renderCollectionTools();
 if(before){const changed=data.stats.files!==before.stats.files || JSON.stringify(data.jobs.map(j=>[j.id,j.state,j.done]))!==JSON.stringify(before.jobs.map(j=>[j.id,j.state,j.done]));if(changed)await loadFiles();}
}
function renderSourceTools(){const s=state.status?.sources.find(s=>s.id===state.source);$('#source-tools').hidden=!s;if(s){$('#source-info').textContent=`${s.mode==='managed'?'托管复制':s.mode==='read-only'?'只读资料包':'就地索引'} · ${s.online?'来源在线':'来源离线'} · ${s.kind==='pack'?'包内原件已校验':'上次扫描 '+date(s.last_scan)}${s.watch?.last_error?' · '+s.watch.last_error:''}${s.watch?.missing?' · '+s.watch.missing+' 份原件缺失':''}`;$('#watch-label').hidden=s.kind==='pack';$('#source-watch').checked=!!s.watch?.enabled;$('#rescan-button').hidden=s.kind==='pack';$('#relocate-source').hidden=s.kind==='pack';$('#rescan-button').disabled=!s.online;}}
function renderCollectionTools(){
 const c=state.status?.collections?.find(c=>c.id===state.collection);
 $('#collection-tools').hidden=!c;
 if(c){
   $('#collection-info').textContent=`第 ${c.depth||1} 级 · 本级 ${c.direct_count??c.count} 份 · 含子分类 ${c.count} 份 · 原件位置不变`;
   $('#new-subcollection').disabled=(c.depth||1)>=9;
   $('#new-subcollection').title=(c.depth||1)>=9?'已到第 9 级':'在此分类下新建子分类';
   $('#include-child-collections').checked=state.includeChildren;
   $('#breadcrumb').textContent=collectionLabel(c);
 }
}
async function openMoveCollection(){
 const cols=state.status?.collections||[],current=cols.find(c=>c.id===state.collection);
 if(!current)return;
 const descendants=collectionDescendants(current.id);
 const height=Math.max(...cols.filter(c=>descendants.has(c.id)).map(c=>c.depth||1))-(current.depth||1)+1;
 const excluded=new Set([...descendants,...cols.filter(c=>(c.depth||1)+height>9).map(c=>c.id)]);
 parentOptions($('#move-collection-parent'),current.parent_id,excluded);
 $('#move-collection-name').textContent=collectionLabel(current);
 showDialog('#move-collection-dialog');
}
async function loadFiles(){
 const seq=++state.seq;const q=new URLSearchParams({q:state.q,source:state.source,collection:state.collection,offset:state.offset,sort:state.sort,camera:state.camera,month:state.month,tag:state.tag,after:state.after,before:state.before,include_children:state.includeChildren?'1':'0'});if(['document','image','video'].includes(state.view))q.set('kind',state.view);if(state.view==='starred')q.set('starred','1');
 $('#file-list').setAttribute('aria-busy','true');
 try{
  const result=await api('/files?'+q);if(seq!==state.seq)return;
  if(state.offset>0&&state.offset>=result.total){state.offset=result.total?Math.floor((result.total-1)/60)*60:0;return await loadFiles();}
  state.files=result.files;state.total=result.total;renderFiles();$('#search-time').textContent=state.q?`${result.elapsed_ms} ms`:'';
 }finally{if(seq===state.seq)$('#file-list').setAttribute('aria-busy','false');}
}
function updateFilterOptions(data){
 for(const [id,key,selected,label] of [['#camera-filter','cameras',state.camera,'设备'],['#month-filter','months',state.month,'月份']]){
  const el=$(id),values=data[key]||[],signature=JSON.stringify(values);
  if(el.dataset.options!==signature){el.innerHTML=`<option value="">全部${label}</option>`+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')+`<option value="__unknown__">${key==='months'?'日期':'设备'}未记录</option>`;el.dataset.options=signature;el.value=selected;}
 }
 $('#reset-filters').hidden=!(state.camera||state.month);syncFilters();
}
function thumbnailHTML(f,cls='row-thumbnail'){
 return f.has_thumbnail?`<img class="${cls}" loading="lazy" decoding="async" src="${mediaURL(f)}" alt="${esc(f.name)}">`:fileIcon(f);
}
function renderFiles(){
 const list=$('#file-list'),grid=state.layout==='grid';list.classList.toggle('gallery',grid&&state.files.length>0);$('.table-head').hidden=grid;
 $('#view-list').classList.toggle('active',!grid);$('#view-grid').classList.toggle('active',grid);$('#view-list').setAttribute('aria-pressed',String(!grid));$('#view-grid').setAttribute('aria-pressed',String(grid));$('#sort-select').value=state.sort;
 $('#result-label').textContent=state.q?`找到 ${state.total} 份资料`:`${state.total} 份资料`;
 if(!state.files.length){const filtered=state.tag||state.after||state.before||state.q||state.source||state.collection||state.view!=='all'||state.camera||state.month;list.innerHTML=`<div class="empty"><div class="empty-art"></div><h3>${filtered?'这里暂时没有资料':'从第一份资料开始'}</h3><p>${filtered?'试试其他关键词或筛选条件。正在导入的文件将在处理完成后出现。':'把散落的文件收在一处。文档、图片与灵感，<br>都可以在这里找到自己的位置。'}</p><button class="primary" id="empty-action">${filtered?'查看全部资料':'＋ 添加第一份资料'}</button></div>`;$('#empty-action').onclick=()=>filtered?setView('all',''):showImport();}
 else if(grid){let previousMonth='';list.innerHTML=state.files.map(f=>{
  const month=f.captured_at?f.captured_at.slice(0,7).replace('-',' 年 ')+' 月':'日期未记录';let heading='';
  if(state.sort==='captured'&&month!==previousMonth){heading=`<div class="month-heading">${esc(month)}</div>`;previousMonth=month;}
  return heading+`<div class="gallery-card ${state.selected.has(f.id)?'selected':''}" data-file="${f.id}" role="button" tabindex="0" aria-label="查看 ${esc(f.name)}"><div class="gallery-image">${thumbnailHTML(f,'gallery-thumb')}<label><input type="checkbox" data-select="${f.id}" ${state.selected.has(f.id)?'checked':''} aria-label="选择 ${esc(f.name)}"></label>${f.starred?'<span class="gallery-star">★</span>':''}${f.kind==='video'?`<span class="video-duration">▷ ${duration(f.duration)||'视频'}</span>`:''}${!f.available?'<span class="gallery-offline">离线预览</span>':''}</div><div class="gallery-copy"><div class="file-name">${highlight(f.name)}</div><p>${esc(f.camera||f.source_name)} <span>${size(f.size)}</span></p><p>${esc(f.captured_at?captureDate(f.captured_at).slice(0,10):'日期未记录')}${f.media_error?' · 预览受限':''}</p>${f.tags.length?`<div class="file-tags">${f.tags.slice(0,2).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>`:''}</div></div>`;
 }).join('');}
 else list.innerHTML=state.files.map(f=>`<div class="file-row ${state.selected.has(f.id)?'selected':''}" data-file="${f.id}" role="button" tabindex="0" aria-label="查看 ${esc(f.name)}"><label><input type="checkbox" data-select="${f.id}" ${state.selected.has(f.id)?'checked':''} aria-label="选择 ${esc(f.name)}"></label><div class="file-main">${thumbnailHTML(f)}<div class="file-copy"><div class="file-name">${f.starred?'<span class="star">★</span>':''}${highlight(f.name)}</div><div class="file-excerpt">${highlight(f.excerpt||[f.captured_at?captureDate(f.captured_at):'',f.camera,f.width?f.width+' × '+f.height:'',duration(f.duration)].filter(Boolean).join(' · ')||f.warning||'已收好这份资料')}</div>${f.tags.length?`<div class="file-tags">${f.tags.slice(0,3).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</div>`:''}</div></div><span class="file-source">${esc(f.source_name)}</span><span class="file-size">${size(f.size)}</span>${statusBadge(f)}</div>`).join('');
 $('#pagination').hidden=state.total<=60;$('#prev-page').disabled=state.offset===0;$('#next-page').disabled=state.offset+60>=state.total;$('#page-number').textContent=`${Math.floor(state.offset/60)+1} / ${Math.max(1,Math.ceil(state.total/60))}`;selectionUI();
}
function selectionUI(){
 const count=state.selected.size;$('#selection-bar').hidden=!count;
 $('#selection-count').textContent=`已选择 ${count} 份`;
 for(const [id,label] of [['#export-selection','导出所选'],['#collect-selection','加入分类'],['#remove-selection','移除所选']]){$(id).hidden=!count;$(id).textContent=label;}
 $('#select-all').checked=state.files.length>0&&state.files.every(f=>state.selected.has(f.id));
 $('#select-all').indeterminate=state.files.some(f=>state.selected.has(f.id))&&!$('#select-all').checked;
}
async function setView(view,source='',collection=''){state.view=view;state.source=source;state.collection=collection;state.includeChildren=true;state.offset=0;state.tag='';state.after='';state.before='';$('#search-options').textContent='搜索工具';filtersExpanded=['image','video'].includes(view);state.camera='';state.month='';$('#camera-filter').value='';$('#month-filter').value='';state.layout=['image','video'].includes(view)?'grid':'list';state.sort=['image','video'].includes(view)?'captured':'added';state.selected.clear();if(!source&&!collection&&view==='all'){state.q='';$('#search').value='';$('#clear-search').hidden=true;}$$('#navigation button').forEach(b=>{const active=!source&&!collection&&b.dataset.view===view;b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});const s=state.status?.sources.find(s=>s.id===source);const c=state.status?.collections?.find(c=>c.id===collection);const title=c?c.name:s?s.name:names[view];$('#page-title').innerHTML=esc(title);$('#breadcrumb').textContent=c?collectionLabel(c):title;$('#page-subtitle').textContent=c?'自定义分类 · 原件位置不变':s?s.root:view==='starred'?'重要的资料，留在手边。':'本地文件索引，让你和 AI 都能找到原文。';renderSourceTools();renderCollectionTools();await refreshStatus();await loadFiles();}
function renderJobs(){const jobs=state.status.jobs;$('#jobs-list').innerHTML=jobs.length?jobs.map(j=>{const result=j.result;let detail=`${date(j.created_at)} · ${j.total?j.done+' / '+j.total:'等待处理'}`;if(result){if(j.kind==='refresh')detail=`已更新 ${result.updated} 个对象${result.failure_count?'，需处理 '+result.failure_count+' 项':''}`;if(j.kind==='import')detail=`已处理 ${result.imported} 份资料${result.failure_count?'，失败 '+result.failure_count+' 份':''}`;if(j.kind==='verify')detail=`校验通过 ${result.verified} 个对象 · 原件离线 ${result.missing} 个 · 内容变化 ${result.changed.length} 个`;if(j.kind==='export')detail=`${result.files} 份资料 · ${size(result.bytes)}\n${result.path}`;if(j.kind==='backup')detail=`备份已保存：${result.path}\n${size(result.bytes)} · 外置原件仍需单独备份`;if(j.kind==='rebuild_knowledge')detail=`已重建 ${result.passages} 段索引，保留固定引用`;if(j.kind==='repository_backup')detail=`快照已保存：${result.path}\n新增 ${size(result.added_bytes)} · 复用 ${size(result.reused_bytes)} · ${result.external_objects} 个外部对象未纳入`;if(j.kind==='ocr')detail=`已识别 ${result.pages} 页 · ${result.lines} 行文字`;if(j.kind==='mount')detail=`${result.files} 份资料已校验并挂载`;}return `<div class="job"><div class="job-top"><strong>${jobNames[j.kind]||esc(j.kind)}</strong><span>${jobStates[j.state]}</span></div><div class="job-progress"><progress max="${j.total||1}" value="${j.state==='done'?(j.total||1):j.done}"></progress></div><div class="job-detail">${esc(detail)}</div>${j.error?`<div class="job-error">${esc(j.error)}</div>`:''}${['failed','partial'].includes(j.state)?`<button class="text-button" data-retry="${j.id}">重试任务</button>`:''}${j.kind==='export'&&j.state==='done'?'<button class="text-button" data-open-exports>打开导出目录 ↗</button>':''}<button class="text-button danger" data-delete-job="${j.id}">${['queued','running'].includes(j.state)?'取消并删除':'删除'}</button></div></div>`;}).join(''):'<div class="empty"><h3>一切就绪</h3><p>导入、导出与校验任务会显示在这里。</p></div>';}
async function readOriginal(id){const result=await api('/open',{id});if(result.preview_url){$('#reader-title').textContent=result.name;$('#reader-frame').src=result.preview_url;showDialog('#reader-dialog');}else if(result.opened==='copy')toast('原件已不在原位置，打开的是资料库中保存的副本');}
async function openDetail(id){
 if(!state.detail)detailFocus=document.activeElement;state.detail=id;const f=await api('/files/'+id);if(state.detail!==id)return;
 $('#detail-scrim').hidden=false;const d=$('#detail');d.hidden=false;const readonly=f.mode==='read-only',isWeb=/\.html?$/i.test(f.name),isSVG=/\.svg$/i.test(f.name);
 const ch=state.q?[...f.chunks].sort((a,b)=>Number(b.text.toLowerCase().includes(state.q.toLowerCase()))-Number(a.text.toLowerCase().includes(state.q.toLowerCase()))):f.chunks;
 let preview='';
 if(f.kind==='image'&&f.has_thumbnail)preview=`<img class="preview-image" src="${mediaURL(f)}" alt="${esc(f.name)}"><p class="note">本地预览 · 原始分辨率 ${f.width||'—'} × ${f.height||'—'}</p>`;
 if(f.kind==='video'){
  if(f.available&&/\.(mp4|mov|m4v|webm)$/i.test(f.name)&&!f.media_error)preview=`<video id="video-player" class="preview-video" controls playsinline preload="metadata" ${f.has_thumbnail?`poster="${mediaURL(f)}"`:''}><source src="${mediaURL(f,'stream')}" type="${esc(f.mime)}"></video><p id="video-help" class="note">直接读取本地原件，不转码。若编码不被系统支持，可用外部播放器打开。</p>`;
  else if(f.has_thumbnail)preview=`<img class="preview-image" src="${mediaURL(f)}" alt="视频封面">`;
 }
 if(isWeb&&f.available)preview=`<iframe class="html-preview" title="网页安全阅读" sandbox="" src="${mediaURL(f,'document')}"></iframe><p class="note">安全阅读保留正文与表格；脚本和外部资源已移除。</p>`;
 const metadata=f.kind==='image'||f.kind==='video'?`<dt>拍摄时间</dt><dd>${esc(captureDate(f.captured_at))}${f.captured_source?`<small class="metadata-source">${esc(f.captured_source)}${f.captured_at&&!/[Zz]|[+-]\d{2}:\d{2}$/.test(f.captured_at)?' · 未记录时区':''}</small>`:''}</dd><dt>拍摄设备</dt><dd>${esc([f.make,f.camera].filter(Boolean).join(' · ')||'文件未记录')}</dd><dt>分辨率</dt><dd>${f.width&&f.height?f.width+' × '+f.height:'文件未记录'}</dd>${f.kind==='video'?`<dt>时长</dt><dd>${f.duration!=null?duration(f.duration)+'（'+f.duration+' 秒）':'无法读取'}</dd><dt>视频编码</dt><dd>${esc(f.codec||'未记录')}</dd>`:''}${f.gps_lat!=null&&f.gps_lon!=null?`<dt>GPS</dt><dd>${f.gps_lat.toFixed(5)}, ${f.gps_lon.toFixed(5)} · 仅本地显示</dd>`:''}`:'';
 const chips=(f.collections||[]).length?f.collections.map(c=>`<span class="collection-chip">${esc(collectionName(c.id)||collectionLabel(c))}<button data-leave-collection="${c.id}" title="从「${esc(c.name)}」移除" aria-label="从分类 ${esc(c.name)} 移除">×</button></span>`).join(''):'<span class="collection-none">还没有归入分类</span>';
 d.innerHTML=`<div class="detail-top"><span class="eyebrow">资料详情</span><button id="close-detail" class="icon-button" aria-label="关闭详情">×</button></div>${fileIcon(f)}<h2 id="detail-title">${esc(f.name)}</h2><span class="detail-subtitle">${size(f.size)} · ${esc(f.source_name)} ${readonly?'· 只读':''}</span><div class="detail-actions">${(f.name.toLowerCase().endsWith('.pdf')||f.kind==='image'&&!isSVG)?'<button id="read-inside" class="secondary">应用内阅读</button><button id="ocr-file" class="secondary">识别文字</button>':''}<button id="open-original" class="primary" ${f.available?'':'disabled'}>${isWeb?'阅读网页':isSVG?'查看矢量图':'打开原件'}</button><button id="reveal-original" class="secondary" ${f.available?'':'disabled'}>定位文件</button><button id="star-file" class="secondary" ${readonly?'disabled':''}>${f.starred?'★ 已收藏':'☆ 收藏'}</button></div>${f.origin_missing&&f.available?'<p class="notice">原件已不在原位置，当前使用资料库中保存的副本。建议导出资料包备份。</p>':''}${!f.available?'<p class="notice">原件当前离线。缩略图、元数据与已提取的文字仍可浏览，连接来源后可打开原件。</p>':''}${f.warning?`<p class="notice">${esc(f.warning)}</p>`:''}${f.media_error?`<p class="notice">${esc(f.media_error)}</p>`:''}${f.integrity==='changed'?'<p class="notice">当前文件与入库时的内容不同，请重新扫描来源。</p>':''}${preview}<dl>${metadata}<dt>加入时间</dt><dd>${date(f.added_at)}</dd><dt>保存方式</dt><dd>${f.mode==='managed'?'托管复制':readonly?'只读资料包':'就地索引'}</dd></dl><div class="detail-section detail-collections"><h3>分类</h3><div class="collection-chips">${chips}</div><button id="add-to-collection" class="text-button">＋ 加入分类</button></div><div class="detail-section"><h3>标签</h3><div class="detail-tags"><textarea id="file-tags" rows="2" placeholder="用逗号分隔，例如：旅行，2026，项目资料" ${readonly?'disabled':''}>${esc(f.tags.join('，'))}</textarea><button id="save-tags" class="secondary" ${readonly?'disabled':''}>保存</button></div></div>${ch.length?`<div class="detail-section"><h3>原文片段 <span class="muted">${f.chunks.length} 段</span></h3>${ch.slice(0,30).map(c=>`<div class="chunk"><button class="chunk-label text-button" data-reader-page="${c.page||1}" data-reader-bounds="${esc(JSON.stringify(c.bounds||null))}">${esc(c.label)} · 字符 ${c.offset+1}${f.name.toLowerCase().endsWith('.pdf')||c.ocr?' · 定位原页':''}</button><p>${highlight(c.text)}</p></div>`).join('')}${ch.length>30?'<p class="note">当前显示前 30 段；搜索关键词可优先显示命中段落。</p>':''}</div>`:''}${f.versions.length?`<p class="note">已保留 ${f.versions.length} 次内容变化记录；旧托管对象不会自动删除。</p>`:''}<details class="advanced-info"><summary>文件信息与管理</summary><dl><dt>原始位置</dt><dd>${esc(f.origin_path)}</dd><dt>内容标识</dt><dd><code>${f.sha}</code></dd><dt>最近校验</dt><dd>${date(f.verified_at)} · ${{ok:'一致',unchecked:'未校验',missing:'原件离线',changed:'内容有变化'}[f.integrity]||esc(f.integrity)}</dd></dl>${preferencePanel(f)}<div class="detail-section detail-remove"><h3>移除资料</h3><p class="note">${f.available?(f.origin_missing?'原件已不在原位置，资料库中的这份副本是唯一一份。移入回收站后仍可恢复；永久清理前请先备份。':'移除后这条记录、标签与原文片段会保留在回收站；原始位置的文件不受影响。'):'原件已离线。移除后这条记录、标签与原文片段会保留在回收站，仍可恢复。'}</p><button id="remove-file" class="secondary danger">从资料库移除</button></div></details>`;
 $('main').inert=true;$('.sidebar').inert=true;if(!d.contains(document.activeElement))$('#close-detail').focus();
 if($('#read-inside'))$('#read-inside').onclick=()=>action(()=>openPageReader(id));
 if($('#ocr-file'))$('#ocr-file').onclick=()=>action(async()=>{await api('/ocr',{id,force:f.chunks.some(c=>c.ocr)});toast('已加入本地文字识别任务');showDialog('#tasks-dialog');});
 $$('#detail [data-reader-page]').forEach(b=>b.onclick=()=>{if(f.name.toLowerCase().endsWith('.pdf')||f.kind==='image')action(()=>openPageReader(id,Number(b.dataset.readerPage),JSON.parse(b.dataset.readerBounds)));});
 $('#close-detail').onclick=closeDetail;$('#open-original').onclick=()=>action(()=>readOriginal(id));$('#reveal-original').onclick=()=>action(async()=>{const r=await api('/open',{id,reveal:true});if(r.opened==='copy')toast('原件已不在原位置，已定位到资料库中的副本');});
 $('#star-file').onclick=()=>action(async()=>{await api('/edit',{id,starred:!f.starred});await openDetail(id);await refreshStatus();await loadFiles();});
 $('#remove-file').onclick=()=>action(()=>removeFiles([id],f.name));
 $('#edit-preference').onclick=()=>action(()=>openPreference(f));
 $('#add-to-collection').onclick=()=>openCollectionPicker([id],(f.collections||[]).map(c=>c.id));
 $$('#detail [data-leave-collection]').forEach(b=>b.onclick=()=>action(()=>leaveCollection(b.dataset.leaveCollection,[id])));
 if(typeof attachNoteEditor==='function')attachNoteEditor(id);
 $('#save-tags').onclick=()=>action(async()=>{await api('/edit',{id,tags:$('#file-tags').value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean)});toast('标签已保存');await loadFiles();});
 const video=$('#video-player');if(video){video.addEventListener('error',()=>{$('#video-help').textContent='系统无法播放此编码，请用“打开原件”交给外部播放器。';});const src=video.querySelector('source');src.addEventListener('error',()=>{$('#video-help').textContent='视频读取失败或编码不被支持，请连接来源或使用外部播放器。';});}
}
function closeDetail(){const video=$('#video-player');if(video){video.pause();video.removeAttribute('src');video.querySelectorAll('source').forEach(s=>s.remove());video.load();}$('#detail').innerHTML='';$('#detail').hidden=true;$('#detail-scrim').hidden=true;state.detail=null;$('main').inert=false;$('.sidebar').inert=false;if(detailFocus?.isConnected)detailFocus.focus();detailFocus=null;}
function pick(kind){if(window.webkit?.messageHandlers?.native){window.webkit.messageHandlers.native.postMessage({action:'pick',kind});}else{toast('浏览器模式下，请在下方粘贴本地路径');$(kind==='pack'?'#pack-path':'#import-paths').focus();}}
window.addEventListener('native-selection',event=>{const {kind,paths}=event.detail;if(['discovery','repository'].includes(kind))return;if(kind==='backup')$('#backup-path').value=paths[0]||'';else if(kind.startsWith('relocate-')){$('#relocate-path').value=paths[0]||'';$('#relocate-apply').disabled=true;}else if(kind==='pack')$('#pack-path').value=paths[0]||'';else{const input=$('#import-paths');const existing=input.value.trim();input.value=(existing?existing+'\n':'')+paths.join('\n');}});
$$('.import-launch').forEach(b=>b.onclick=showImport);$$('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());$('#reader-dialog').addEventListener('close',()=>$('#reader-frame').removeAttribute('src'));$$('dialog').forEach(d=>d.addEventListener('click',e=>{if(e.target===d){const r=d.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close();}}));
$('#mount-button').onclick=showMount;$('#pick-files').onclick=()=>pick('files');$('#pick-folder').onclick=()=>pick('folder');$('#pick-pack').onclick=()=>pick('pack');
$('#settings-button').onclick=showSettings;
window.addEventListener('native-open-settings',()=>{if(state.detail)closeDetail();showSettings();});
$$('.settings-back').forEach(button=>button.onclick=showSettings);
$$('#settings-dialog,.settings-page').forEach(dialog=>dialog.addEventListener('close',()=>{if(!$$('dialog[open]').length&&!state.detail)$('#settings-button').focus();}));
$('#start-import').onclick=async()=>{const button=$('#start-import');button.disabled=true;$('#import-error').textContent='';try{const paths=$('#import-paths').value.split('\n').map(s=>s.trim()).filter(Boolean);await api('/import',{paths,mode:$('input[name=mode]:checked').value});$('#import-dialog').close();$('#import-paths').value='';toast('已加入后台任务，文件将陆续出现');await refreshStatus();}catch(e){$('#import-error').textContent=e.message;}finally{button.disabled=false;}};
$('#start-mount').onclick=async()=>{const button=$('#start-mount');button.disabled=true;$('#mount-error').textContent='';try{if(!$('#pack-path').value.trim())throw Error('请先选择资料包');await api('/mount',{path:$('#pack-path').value.trim()});$('#mount-dialog').close();toast('开始校验资料包');showDialog('#tasks-dialog');await refreshStatus();}catch(e){$('#mount-error').textContent=e.message;}finally{button.disabled=false;}};
$('#navigation').onclick=e=>{const b=e.target.closest('[data-view]');if(b)action(()=>setView(b.dataset.view));};$('#sources').onclick=e=>{const remove=e.target.closest('[data-remove-source]');if(remove){action(()=>disconnectSource(remove.dataset.removeSource));return;}const b=e.target.closest('[data-source]');if(b)action(()=>setView('all',b.dataset.source));};
$('#sources').onkeydown=e=>{if(!['Enter',' '].includes(e.key))return;const b=e.target.closest('[data-source]');if(b&&e.target===b){e.preventDefault();action(()=>setView('all',b.dataset.source));}};
$('#collections').onclick=e=>{
 if(collectionDragId||collectionMovePending)return;
 const toggle=e.target.closest('[data-toggle-collection]');
 if(toggle){toggleCollection(toggle.dataset.toggleCollection);return;}
 const add=e.target.closest('[data-child-collection]');
 if(add){openCollectionPicker([],[],add.dataset.childCollection);return;}
 const row=e.target.closest('[data-collection]');
 if(row)action(()=>setView('collection','',row.dataset.collection));
};
$('#collections').onkeydown=e=>{
 const row=e.target.closest('[data-collection]');
 if(!row||e.target!==row)return;
 const cid=row.dataset.collection,cols=state.status?.collections||[],current=cols.find(c=>c.id===cid);
 if(e.altKey&&['ArrowUp','ArrowDown'].includes(e.key)){
   e.preventDefault();const siblings=cols.filter(c=>(c.parent_id||null)===(current?.parent_id||null));
   const target=siblings[siblings.indexOf(current)+(e.key==='ArrowUp'?-1:1)];
   if(target)applyCollectionDrop(collectionDropPlan(cid,target.id,e.key==='ArrowUp'?'before':'after'));
   return;
 }
 if(['Enter',' '].includes(e.key)){e.preventDefault();action(()=>setView('collection','',cid));return;}
 if(e.key==='ArrowRight'&&current?.child_count){e.preventDefault();if(collapsedCollections.has(cid))toggleCollection(cid);else $(`#collections [data-collection="${cols.find(c=>c.parent_id===cid)?.id}"]`)?.focus();}
 if(e.key==='ArrowLeft'){e.preventDefault();if(current?.child_count&&!collapsedCollections.has(cid))toggleCollection(cid);else $(`#collections [data-collection="${current?.parent_id}"]`)?.focus();}
 if(['ArrowUp','ArrowDown'].includes(e.key)){e.preventDefault();const rows=$$('#collections [data-collection]'),index=rows.indexOf(row);rows[index+(e.key==='ArrowUp'?-1:1)]?.focus();}
};
$('#new-subcollection').onclick=()=>openCollectionPicker([],[],state.collection);
$('#move-collection').onclick=()=>action(openMoveCollection);
$('#save-collection-move').onclick=()=>action(async()=>{
 const cid=state.collection;
 await api('/collection/move',{collection_id:cid,parent_id:$('#move-collection-parent').value||null});
 $('#move-collection-dialog').close();await refreshStatus();revealCollection(cid);await setView('collection','',cid);toast('分类已移动');
});
$('#include-child-collections').onchange=()=>{state.includeChildren=$('#include-child-collections').checked;state.offset=0;state.selected.clear();action(loadFiles);};
$('#new-collection').onclick=()=>openCollectionPicker([]);
$$('[data-fold]').forEach(b=>b.onclick=()=>toggleFold(b.dataset.fold));
$('#collection-choices').onclick=e=>{const b=e.target.closest('[data-pick-collection]');if(!b)return;if(collectionTarget.length)action(()=>assignToCollection({collection_id:b.dataset.pickCollection}));else{$('#collection-dialog').close();action(()=>setView('collection','',b.dataset.pickCollection));}};
$('#create-collection').onclick=()=>action(createCollectionFromDialog);
$('#new-collection-name').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();action(createCollectionFromDialog);}};
$('#collection-done').onclick=()=>$('#collection-dialog').close();
$('#collect-selection').onclick=()=>{const ids=[...state.selected];if(ids.length)openCollectionPicker(ids);};
$('#rename-collection').onclick=()=>action(renameCollection);
$('#delete-collection-button').onclick=()=>action(deleteCollection);
$('#rename-cancel').onclick=()=>$('#rename-dialog').close();
$('#rename-save').onclick=()=>action(async()=>{
  const name=$('#rename-input').value.trim();
  if(!name){$('#rename-error').textContent='请输入分类名称';return;}
  const result=await api('/collection/rename',{collection_id:state.collection,name});
  $('#rename-dialog').close();
  toast(`已重命名为「${result.name}」`);
  await refreshStatus();
  $('#page-title').innerHTML=esc(result.name);
  $('#breadcrumb').textContent=result.name;
});
$('#rename-input').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$('#rename-save').click();}};
$('#confirm-ok').onclick=()=>settleConfirm(true);
$('#confirm-cancel').onclick=()=>settleConfirm(false);
$('#confirm-dialog').addEventListener('close',()=>settleConfirm(false));
$('#file-list').onclick=e=>{const input=e.target.closest('input[data-select]');if(input){input.checked?state.selected.add(input.dataset.select):state.selected.delete(input.dataset.select);renderFiles();return;}if(e.target.closest('label'))return;const row=e.target.closest('[data-file]');if(row)action(()=>openDetail(row.dataset.file));};
$('#file-list').onkeydown=e=>{if(['Enter',' '].includes(e.key)&&e.target.matches('[data-file]')){e.preventDefault();action(()=>openDetail(e.target.dataset.file));}};
$('#select-all').onchange=e=>{state.files.forEach(f=>e.target.checked?state.selected.add(f.id):state.selected.delete(f.id));renderFiles();};
let debounce;$('#search').oninput=e=>{state.q=e.target.value;state.offset=0;state.selected.clear();$('#clear-search').hidden=!state.q;clearTimeout(debounce);debounce=setTimeout(()=>action(loadFiles),180);};$('#clear-search').onclick=()=>{$('#search').value='';$('#search').dispatchEvent(new Event('input'));$('#search').focus();};
$('#refresh-button').onclick=()=>action(async()=>{await refreshStatus();await loadFiles();});$('#rescan-button').onclick=()=>action(async()=>{await api('/rescan',{source_id:state.source,incremental:true});toast('已开始增量扫描');await refreshStatus();});$('#remove-source-button').onclick=()=>{if(state.source)action(()=>disconnectSource(state.source));};
$('#prev-page').onclick=()=>{state.offset=Math.max(0,state.offset-60);action(loadFiles);};$('#next-page').onclick=()=>{state.offset+=60;action(loadFiles);};
$('#tasks-button').onclick=()=>showDialog('#tasks-dialog');$('#storage-button').onclick=()=>showDialog('#storage-dialog');$('#detail-scrim').onclick=closeDetail;
$('#pause-button').onclick=()=>action(async()=>{await api('/pause',{paused:!state.status.paused});await refreshStatus();});
$('#jobs-list').onclick=e=>{const retry=e.target.closest('[data-retry]');if(retry)action(async()=>{await api('/retry',{job_id:retry.dataset.retry});await refreshStatus();});const del=e.target.closest('[data-delete-job]');if(del)action(()=>deleteJob(del.dataset.deleteJob));if(e.target.closest('[data-open-exports]'))action(()=>api('/reveal',{target:'exports'}));};
async function exportFiles(ids){await api('/export',{ids});$('#storage-dialog').close();showDialog('#tasks-dialog');toast('正在生成完整资料包');await refreshStatus();}
$('#export-all').onclick=()=>action(()=>exportFiles([]));$('#export-selection').onclick=()=>action(()=>exportFiles([...state.selected]));$('#remove-selection').onclick=()=>{const ids=[...state.selected];if(ids.length)action(()=>removeFiles(ids));};$('#verify-all').onclick=()=>action(async()=>{await api('/verify',{});$('#storage-dialog').close();showDialog('#tasks-dialog');await refreshStatus();});$('#open-data').onclick=()=>action(()=>api('/reveal',{target:'data'}));$('#open-manifest').onclick=()=>action(()=>api('/reveal',{target:'manifest'}));
document.addEventListener('keydown',e=>{
 if($$('dialog').some(d=>d.open))return;
 if(state.detail){
  if(e.key==='Escape'){e.preventDefault();closeDetail();return;}
  if(e.key==='Tab'){
   const controls=$$('#detail button:not(:disabled),#detail textarea:not(:disabled),#detail summary,#detail video[controls]').filter(el=>!el.closest('details:not([open])')||el.tagName==='SUMMARY');
   const first=controls[0],last=controls[controls.length-1];
   if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}
   else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}
  }
  return;
 }
 if((e.metaKey||e.ctrlKey)&&e.key.toLowerCase()==='k'){e.preventDefault();$('#search').focus();}
});
async function boot(){try{await refreshStatus();await loadFiles();if(location.hash==='#settings')showSettings();}catch(e){toast('连接本地资料库失败：'+e.message,true);$('#footer-status').textContent='服务连接失败';}}
applyFold('collections');applyFold('sources');
boot();setInterval(async()=>{if(state.busy||collectionDragId||collectionMovePending||document.hidden)return;state.busy=true;try{await refreshStatus();}catch(e){$('#footer-status').textContent='本地服务暂时不可用';}finally{state.busy=false;}},2000);

$('#file-list').addEventListener('error',e=>{if(e.target.tagName==='IMG'){e.target.classList.add('preview-failed');e.target.alt='预览暂不可用';}},true);
$('#view-list').onclick=()=>{state.layout='list';renderFiles();};$('#view-grid').onclick=()=>{state.layout='grid';renderFiles();};
$('#sort-select').onchange=e=>{state.sort=e.target.value;state.offset=0;action(loadFiles);};
for(const [id,key] of [['#camera-filter','camera'],['#month-filter','month']])$(id).onchange=e=>{state[key]=e.target.value;state.offset=0;state.selected.clear();updateFilterOptions(state.status);action(loadFiles);};
$('#reset-filters').onclick=()=>{state.selected.clear();state.camera='';state.month='';$('#camera-filter').value='';$('#month-filter').value='';state.offset=0;updateFilterOptions(state.status);action(loadFiles);};
$('#refresh-derived').onclick=()=>action(async()=>{await api('/refresh',{});$('#storage-dialog').close();toast('已安排更新照片、视频与网页索引');showDialog('#tasks-dialog');await refreshStatus();});

$('#filter-toggle').onclick=()=>{filtersExpanded=!($('#filter-toggle').getAttribute('aria-expanded')==='true');syncFilters();};
$('#clear-selection').onclick=()=>{state.selected.clear();renderFiles();};
$('#navigation [data-view=all]').setAttribute('aria-current','page');
