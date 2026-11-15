'use strict';
// Management tools stay in dialogs so the main reading space remains simple.
async function showHealth(){
 showDialog('#health-dialog');$('#health-body').textContent='正在检查…';
 const h=await api('/health');
 const sections=[['当前无法打开',h.offline],['原位置缺失，仅有托管副本',h.unique],['内容有变化',h.changed],['托管内容缺失',h.missing]];
 const coverage=h.backup_coverage;
 const coverageText=coverage?`<p class="muted">托管对象 ${coverage.managed_objects} 个 · ${size(coverage.managed_bytes)}。当前有 ${coverage.external_files} 条资料记录的外部原件未纳入库内备份 · ${size(coverage.external_bytes)}（按记录累计），请备份到独立介质。</p>`:'';
 const verification=h.last_restore_verification;
 const indexHealth=h.knowledge?`<p class="muted">${h.knowledge.passages} 段原文 · ${h.knowledge.extractions} 个提取批次 · ${h.knowledge.notes} 份笔记 · ${h.knowledge.counts_consistent?'索引计数一致':'索引计数异常，建议重建'}</p><button id="rebuild-knowledge" class="secondary">重建检索索引</button>`:'';
 $('#health-body').innerHTML=`<p class="muted">备份：${h.last_backup?date(h.last_backup):'尚无备份'} · 可用空间 ${size(h.disk_free)} · ${h.failed_jobs} 项任务需处理</p>${coverageText}${indexHealth}<p class="muted">最近恢复校验：${verification?date(verification.verified_at):'尚无记录'}</p>`+sections.map(([label,files])=>`<section class="health-section"><h3>${label} <small>${files.length}</small></h3>${files.length?files.slice(0,100).map(f=>`<button class="health-file" data-health-file="${esc(f.id)}">${esc(f.name)}</button>`).join(''):'<p class="muted">没有发现</p>'}</section>`).join('');
}
$('#health-button').onclick=()=>action(showHealth);
$('#health-body').onclick=e=>{if(e.target.closest('#rebuild-knowledge')){action(async()=>{await api('/knowledge/rebuild',{});$('#health-dialog').close();showDialog('#tasks-dialog');await refreshStatus();});return;}const b=e.target.closest('[data-health-file]');if(b){$('#health-dialog').close();action(()=>openDetail(b.dataset.healthFile));}};
$('#health-verify').onclick=()=>action(async()=>{await api('/verify',{});$('#health-dialog').close();showDialog('#tasks-dialog');await refreshStatus();});
async function showTrash(){
 showDialog('#trash-dialog');const batches=await api('/trash');
 $('#trash-body').innerHTML=batches.length?batches.map(b=>`<div class="storage-item"><div><strong>${esc(b.label)}</strong><p>${b.files} 份 · ${date(b.created_at)}</p></div><div><button class="text-button" data-restore-trash="${b.id}">恢复</button><button class="text-button danger" data-purge-trash="${b.id}">永久清理</button></div></div>`).join(''):'<p class="muted">回收站为空。</p>';
}
$('#trash-button').onclick=()=>action(showTrash);
$('#trash-body').onclick=e=>action(async()=>{
 const restore=e.target.closest('[data-restore-trash]'),purge=e.target.closest('[data-purge-trash]');
 if(restore){const r=await api('/trash/restore',{id:restore.dataset.restoreTrash});toast(`已恢复 ${r.files} 份资料`);}
 else if(purge){
  if(!await confirmAction({title:'永久清理这批资料？',message:'此操作无法撤销。',details:['记录、标签与不再被引用的托管副本将永久删除。','如果副本是唯一一份内容，请先导出备份。原始位置的文件不受影响。'],ok:'永久清理'}))return;
  await api('/trash/purge',{id:purge.dataset.purgeTrash});toast('已永久清理');
 }else return;
 await refreshStatus();await loadFiles();await showTrash();
});
async function showBackups(){
 $('#storage-dialog').close();showDialog('#backup-dialog');
 const [backups,health]=await Promise.all([api('/backups'),api('/health')]);
 $('#auto-backup').checked=health.auto_backup;
 $('#backup-list').innerHTML=backups.length?backups.map(b=>`<div class="storage-item"><div><strong>${esc(b.name)}</strong><p>${size(b.bytes)}</p></div><button class="text-button" data-restore-backup="${esc(b.path)}">恢复</button></div>`).join(''):'<p class="muted">尚无备份，先创建一份。</p>';
}
$('#backup-manager').onclick=()=>action(showBackups);
$('#backup-now').onclick=()=>action(async()=>{await api('/backup',{});$('#backup-dialog').close();showDialog('#tasks-dialog');toast('已安排完整库备份');await refreshStatus();});
$('#auto-backup').onchange=e=>action(()=>api('/backup/settings',{enabled:e.target.checked}));
async function restoreBackup(path){
 if(!path.trim())throw Error('请选择或粘贴备份路径');
 if(!await confirmAction({title:'校验并恢复这份备份？',message:path,details:['恢复创建独立资料库，当前库不会被覆盖。','备份可能含私人原始路径。只恢复你信任的备份。'],ok:'校验并恢复'}))return;
 $('#restore-backup').disabled=true;$('#restore-result').textContent='正在验证并恢复…';
 try{
  const r=await api('/backup/restore',{path});
  $('#restore-result').innerHTML=`<p class="muted">恢复已保存到独立资料库。${r.verification_recorded===false?'恢复校验历史未能保存。':''}</p><code>${esc(r.path)}</code><button id="switch-restored" class="primary">切换到恢复库</button>`;
  $('#switch-restored').onclick=()=>{
   if(window.webkit?.messageHandlers?.native){window.webkit.messageHandlers.native.postMessage({action:'switch-library',path:r.path});}
   else toast('恢复库已保存。浏览器模式可使用 --data 指向上方目录启动服务。');
  };
 }finally{$('#restore-backup').disabled=false;}
}
$('#restore-backup').onclick=()=>action(()=>restoreBackup($('#backup-path').value));
$('#backup-list').onclick=e=>{const b=e.target.closest('[data-restore-backup]');if(b)action(()=>restoreBackup(b.dataset.restoreBackup));};
$('#relocate-source').onclick=()=>{$('#relocate-path').value='';$('#relocate-preview').textContent='';$('#relocate-apply').disabled=true;showDialog('#relocate-dialog');};
$('#relocate-path').oninput=()=>$('#relocate-apply').disabled=true;
$('#relocate-preview-button').onclick=()=>action(async()=>{
 const r=await api('/source/relocate',{source_id:state.source,path:$('#relocate-path').value});
 $('#relocate-preview').textContent=`${r.matched} 份内容一致 · ${r.changed.length} 份内容不同 · ${r.missing.length} 份未找到。只有一致的文件会重新关联。`;
 $('#relocate-apply').disabled=!r.matched;
});
$('#relocate-apply').onclick=()=>action(async()=>{
 $('#relocate-apply').disabled=true;
 try{const r=await api('/source/relocate',{source_id:state.source,path:$('#relocate-path').value,apply:true});$('#relocate-dialog').close();toast(`已重新关联 ${r.matched} 份资料`);await refreshStatus();await loadFiles();}
 finally{$('#relocate-apply').disabled=false;}
});
function currentFilters(){return {q:state.q,kind:['document','image','video'].includes(state.view)?state.view:'',source:state.source,collection:state.collection,include_children:state.includeChildren?'1':'0',starred:state.view==='starred'?'1':'',sort:state.sort,camera:state.camera,month:state.month,tag:state.tag,after:state.after,before:state.before};}
async function renderSavedSearches(){
 const rows=await api('/searches');
 $('#saved-search-list').innerHTML=rows.length?rows.map(r=>`<div class="storage-item"><button class="text-button" data-load-search="${r.id}">${esc(r.name)}</button><button class="text-button danger" data-delete-search="${r.id}">删除</button></div>`).join(''):'<p class="muted">保存关键词、类型、来源、标签和日期条件，之后一键更新结果。</p>';
 renderSavedSearches.rows=rows;
}
$('#search-options').onclick=()=>action(async()=>{for(const k of ['tag','after','before'])$('#'+k+'-filter').value=state[k];showDialog('#search-dialog');await renderSavedSearches();});
$('#apply-advanced').onclick=()=>action(async()=>{
 const after=$('#after-filter').value,before=$('#before-filter').value;
 if(after&&before&&after>before)throw Error('起日不能晚于止日');
 state.tag=$('#tag-filter').value.trim();state.after=after;state.before=before;state.offset=0;state.selected.clear();
 $('#search-options').textContent=state.tag||after||before?'搜索工具 · 已筛选':'搜索工具';$('#search-dialog').close();await loadFiles();
});
$('#clear-advanced').onclick=()=>{['tag','after','before'].forEach(k=>$('#'+k+'-filter').value='');$('#apply-advanced').click();};
$('#save-search').onclick=()=>action(async()=>{
 // Include unapplied form changes, so saving what is on screen is predictable.
 const filters={...currentFilters(),tag:$('#tag-filter').value.trim(),after:$('#after-filter').value,before:$('#before-filter').value};
 await api('/search/save',{name:$('#search-name').value.trim(),filters});$('#search-name').value='';await renderSavedSearches();toast('已保存智能分类');
});
$('#saved-search-list').onclick=e=>action(async()=>{
 const load=e.target.closest('[data-load-search]'),del=e.target.closest('[data-delete-search]');
 if(del){await api('/search/delete',{id:del.dataset.deleteSearch});await renderSavedSearches();return;}
 if(!load)return;const saved=renderSavedSearches.rows.find(r=>r.id===load.dataset.loadSearch);if(!saved)return;
 const f=saved.filters;await setView(f.starred==='1'?'starred':f.kind||'all',f.source||'',f.collection||'');state.includeChildren=f.include_children!=='0';$('#include-child-collections').checked=state.includeChildren;
 Object.assign(state,{q:f.q||'',sort:f.sort||'added',camera:f.camera||'',month:f.month||'',tag:f.tag||'',after:f.after||'',before:f.before||'',offset:0});
 $('#search').value=state.q;$('#clear-search').hidden=!state.q;$('#camera-filter').value=state.camera;$('#month-filter').value=state.month;
 $('#search-options').textContent=state.tag||state.after||state.before?'搜索工具 · 已筛选':'搜索工具';filtersExpanded=Boolean(state.camera||state.month);syncFilters();
 $('#search-dialog').close();await loadFiles();
});
const pageReader={id:'',sha:'',pages:1,page:1,bounds:null,seq:0};
async function openPageReader(id,page=0,bounds=null){
 const sequence=++pageReader.seq;pageReader.id=id;
 const info=await api('/files/'+id+'/reader');if(sequence!==pageReader.seq)return;
 const f=state.files.find(f=>f.id===id);$('#page-reader-title').textContent=f?.name||'资料阅读';
 pageReader.sha=info.sha||'';pageReader.pages=info.pages;pageReader.page=Math.max(1,Math.min(page||info.page||1,info.pages));pageReader.bounds=bounds;
 showDialog('#page-reader');renderPageReader();
}
function renderPageReader(){
 const r=pageReader;$('#reader-page').value=r.page;$('#reader-page').max=r.pages;$('#reader-pages').textContent=`/ ${r.pages}`;
 $('#reader-prev').disabled=r.page<=1;$('#reader-next').disabled=r.page>=r.pages;
 $('#reader-message').textContent='正在生成本地预览…';$('#reader-highlight').hidden=true;
 const image=$('#reader-image');image.onload=()=>{
  $('#reader-message').textContent='阅读位置已保存在本机';$('#reader-scroll').scrollTop=0;
  const b=pageReader.bounds;if(b){const h=$('#reader-highlight');h.hidden=false;h.style.left=(b[0]*100)+'%';h.style.top=((1-b[1]-b[3])*100)+'%';h.style.width=(b[2]*100)+'%';h.style.height=(b[3]*100)+'%';h.scrollIntoView?.({block:'center'});}
 };
 image.onerror=()=>$('#reader-message').textContent='页面读取失败，请确认原件在线后重试。';
 image.src=`/api/files/${r.id}/page?page=${r.page}&v=${r.sha}`;
 action(()=>api('/reading',{id:r.id,page:r.page}));
}
function goReaderPage(page){if(!Number.isInteger(page)||page<1||page>pageReader.pages){$('#reader-page').value=pageReader.page;return;}pageReader.page=page;pageReader.bounds=null;renderPageReader();}
$('#reader-prev').onclick=()=>goReaderPage(pageReader.page-1);$('#reader-next').onclick=()=>goReaderPage(pageReader.page+1);
$('#reader-page').onchange=e=>goReaderPage(Number(e.target.value));
$('#page-reader').addEventListener('close',()=>{$('#reader-image').removeAttribute('src');pageReader.id='';++pageReader.seq;});

function pickManagement(kind,field){
 if(window.webkit?.messageHandlers?.native)window.webkit.messageHandlers.native.postMessage({action:'pick',kind});
 else{$(field).focus();toast('浏览器模式下，请粘贴本地绝对路径');}
}
$('#pick-backup').onclick=()=>pickManagement('backup','#backup-path');
$('#pick-relocate').onclick=()=>pickManagement(state.status?.sources.find(s=>s.id===state.source)?.kind==='file'?'relocate-file':'relocate-folder','#relocate-path');

function receiveDroppedPaths(paths){
 if(!Array.isArray(paths)||!paths.length||paths.length>1000||paths.some(p=>typeof p!=='string'||!p.startsWith('/')||/[\r\n\0]/.test(p))){toast('请拖入 1–1000 个本机文件或文件夹',true);return;}
 if($$('dialog[open]').some(d=>d.id!=='import-dialog')){toast('请先关闭当前对话框再拖入文件',true);return;}
 showImport();const input=$('#import-paths');input.value=[...new Set([...input.value.split('\n').filter(Boolean),...paths])].join('\n');
 toast(`已接收 ${paths.length} 个路径，确认保存方式后开始索引`);
}
window.addEventListener('native-drop',e=>receiveDroppedPaths(e.detail?.paths));
window.addEventListener('native-drag-state',e=>$('#drop-overlay').hidden=!e.detail?.active);
// Prevent WebKit navigating to a dropped file. Real paths come only from the native pasteboard.
document.addEventListener('dragover',e=>{if([...e.dataTransfer?.types||[]].includes('Files'))e.preventDefault();});
document.addEventListener('drop',e=>{e.preventDefault();$('#drop-overlay').hidden=true;if(collectionDragId){endCollectionDrag();renderCollectionTree();return;}if(!window.webkit?.messageHandlers?.native)toast('请在 Mac 应用中拖入文件，浏览器模式可粘贴绝对路径',true);});
let knowledgeProfiles=[], knowledgeProfile='default', evidence=null;
async function loadKnowledgeProfile(){
 const profile=knowledgeProfiles.find(p=>p.id===knowledgeProfile);
 if(!profile)return;
 $('#profile-name').value=profile.name;$('#profile-enabled').checked=!!profile.enabled;$('#profile-all').checked=!!profile.all_files;
 $('#profile-selectors').hidden=!!profile.all_files;
 const options=(items,chosen,kind)=>items.map(item=>`<label><input type="checkbox" data-scope-${kind}="${esc(item.id)}" ${chosen.includes(item.id)?'checked':''}>${esc(item.path_label||item.name)}</label>`).join('')||'<p class="muted">尚无可选项</p>';
 $('#profile-sources').innerHTML=options(state.status?.sources||[],profile.source_ids||[],'source');
 $('#profile-collections').innerHTML=options(state.status?.collections||[],profile.collection_ids||[],'collection');
 const q='?profile='+encodeURIComponent(knowledgeProfile);
 $('#knowledge-config').value=JSON.stringify(await api('/knowledge/config'+q),null,2);
 if(profile.enabled){const info=await api('/knowledge/info'+q);$('#knowledge-summary').textContent=`${info.files} 份资料 · ${info.passages||0} 段证据 · ${profile.all_files?'全部有效资料':'限定范围'} · 只读`;}
 else $('#knowledge-summary').textContent='此范围已撤销，AI 无法继续读取。';
 const filter=$('#knowledge-collection'),previous=filter.value;filter.innerHTML='<option value="">当前范围内全部分类</option>'+(state.status?.collections||[]).map(c=>`<option value="${esc(c.id)}">${esc(c.path_label||c.name)}</option>`).join('');filter.value=previous;
 $('#knowledge-results').innerHTML='';
}
async function loadKnowledgeProfiles(){
 knowledgeProfiles=await api('/knowledge/profiles');
 if(!knowledgeProfiles.some(p=>p.id===knowledgeProfile))knowledgeProfile=knowledgeProfiles[0]?.id||'default';
 $('#knowledge-profile').innerHTML=knowledgeProfiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}${p.enabled?'':'（已撤销）'}</option>`).join('');
 $('#knowledge-profile').value=knowledgeProfile;await loadKnowledgeProfile();
}
function knowledgeView(evidenceVisible){$('#knowledge-search-view').hidden=evidenceVisible;$('#evidence-view').hidden=!evidenceVisible;}
$('#knowledge-button').onclick=()=>action(async()=>{knowledgeView(false);showDialog('#knowledge-dialog');await loadKnowledgeProfiles();});
$('#knowledge-profile').onchange=e=>action(async()=>{knowledgeProfile=e.target.value;await loadKnowledgeProfile();});
$('#profile-all').onchange=e=>$('#profile-selectors').hidden=e.target.checked;
$('#new-knowledge-profile').onclick=()=>action(async()=>{const r=await api('/knowledge/profile',{name:'新的访问范围',all_files:false,source_ids:[],collection_ids:[],enabled:true});knowledgeProfile=r.id;await loadKnowledgeProfiles();$('#knowledge-scope').open=true;$('#profile-name').focus();});
$('#save-knowledge-profile').onclick=()=>action(async()=>{
 await api('/knowledge/profile',{id:knowledgeProfile,name:$('#profile-name').value,enabled:$('#profile-enabled').checked,all_files:$('#profile-all').checked,source_ids:$$('[data-scope-source]:checked').map(e=>e.dataset.scopeSource),collection_ids:$$('[data-scope-collection]:checked').map(e=>e.dataset.scopeCollection)});
 await loadKnowledgeProfiles();toast('访问范围已更新');
});
$('#knowledge-search-form').onsubmit=e=>{e.preventDefault();action(async()=>{
 $('#knowledge-results').textContent='正在检索…';
 try{const params=new URLSearchParams({profile:knowledgeProfile,q:$('#knowledge-query').value,include_children:String($('#knowledge-children').checked),include_paths:String($('#knowledge-paths').checked)});if($('#knowledge-collection').value)params.set('collection_ids',JSON.stringify([$('#knowledge-collection').value]));const tags=$('#knowledge-tags').value.split(/[,，]/).map(x=>x.trim()).filter(Boolean);if(tags.length)params.set('tags',JSON.stringify(tags));const data=await api('/knowledge/search?'+params);
 $('#knowledge-results').innerHTML=data.results.length?data.results.map(r=>`<article class="evidence-result"><button class="text-button" data-evidence-file="${esc(r.id)}" data-evidence-passage="${esc(r.passage_id||'')}">${esc(r.title)}${r.page?' · 第 '+r.page+' 页':''}</button><small class="result-location">${esc((r.collections||[]).map(c=>c.path_label).join(' · ')||'未归类')}${r.origin_path?' · '+esc(r.origin_path):''}</small>${r.preferred_file?`<p class="note">已指定优先条目：${esc(r.preferred_file.title)} · ${esc(r.preferred_file.reason)}</p>`:''}<p>${esc(r.snippet||'文件名、标签或跨段匹配；打开资料查看上下文。')}</p></article>`).join(''):'<p class="muted">在当前范围内没有找到。</p>';
 }catch(error){$('#knowledge-results').textContent=error.message;throw error;}
 });};
$('#knowledge-results').onclick=e=>{const b=e.target.closest('[data-evidence-file]');if(b)action(async()=>{if(b.dataset.evidencePassage)await openEvidence(b.dataset.evidenceFile,b.dataset.evidencePassage);else{$('#knowledge-dialog').close();await openDetail(b.dataset.evidenceFile);}});};
async function openEvidence(id,passage,expectedSha=null,expectedExtract=null){
 evidence=await api('/citation?id='+encodeURIComponent(id)+'&passage='+encodeURIComponent(passage));
 if((expectedSha&&evidence.sha256!==expectedSha)||(expectedExtract&&evidence.extraction_sha256!==expectedExtract))throw Error('引用版本与固定段落不一致');
 await renderEvidence(id);
}
async function renderEvidence(id){
 if(state.detail)closeDetail();
 $$('dialog[open]').filter(d=>d.id!=='knowledge-dialog').forEach(d=>d.close());
 $('#evidence-title').textContent=evidence.title;
 $('#evidence-status').textContent=evidence.historical?'历史提取版本 · 已保留当时原文':'当前提取版本 · 固定段落引用';
 $('#evidence-body').innerHTML=evidence.passages.map(p=>`<article class="chunk ${p.is_target?'evidence-target':''}"><strong>${esc(p.label||'原文')}${p.is_target?' · 引用段落':''}</strong><p>${esc(p.text)}</p></article>`).join('');
 const target=evidence.passages.find(p=>p.is_target);$('#evidence-page').disabled=evidence.historical||!target?.page;
 $('#evidence-back').onclick=()=>{knowledgeView(false);$('#knowledge-query').focus();};
 $('#evidence-page').onclick=()=>action(async()=>{$('#knowledge-dialog').close();await openPageReader(id,target.page,target.bounds);});
 $('#evidence-file').onclick=()=>action(async()=>{$('#knowledge-dialog').close();await openDetail(id);});
 $('#evidence-copy').onclick=()=>action(async()=>{await navigator.clipboard.writeText(target.citation);toast('固定引用已复制');});
 // Keep evidence and search in one modal so native WebKit retains its AX focus.
 knowledgeView(true);showDialog('#knowledge-dialog');$('#evidence-back').focus();
}
$('#source-watch').onchange=e=>action(async()=>{try{await api('/source/watch',{source_id:state.source,enabled:e.target.checked});toast(e.target.checked?'已开启自动更新，应用运行时会监听并补扫':'已关闭自动更新');}finally{await refreshStatus();}});
async function attachNoteEditor(id){
 const container=document.createElement('details');container.className='detail-section note-editor';container.innerHTML='<summary>我的笔记</summary><textarea rows="6" aria-label="资料笔记" placeholder="记录理解、摘录和待办…" maxlength="100000"></textarea><button class="secondary">保存笔记</button><p class="note">笔记保存在本机资料库，随备份迁移。</p>';
 $('#detail .advanced-info')?.before(container);
 let saved;
 try{saved=await api('/note?id='+encodeURIComponent(id));if(state.detail!==id||!container.isConnected)return;container.querySelector('textarea').value=saved.body;container.open=!!saved.body;}
 catch(e){container.querySelector('p').textContent=e.message;return;}
 container.querySelector('button').onclick=()=>action(async()=>{saved=await api('/note',{id,body:container.querySelector('textarea').value,expected_updated_at:saved.updated_at});toast('笔记已保存');});
}
window.addEventListener('native-citation',e=>action(async()=>{
 const u=new URL(e.detail.url),parts=u.pathname.split('/').filter(Boolean);
 if(u.protocol!=='shiyue:'||u.hostname!=='files'||!parts[0])throw Error('引用地址无效');
 if(parts[1]==='passages'){await openEvidence(parts[0],parts[2],u.searchParams.get('sha'),u.searchParams.get('extract'));return;}
 if(parts[1]==='chunks'&&/^\d+$/.test(parts[2])){
  const q=new URLSearchParams({id:parts[0],start:parts[2]});
  for(const [a,b] of [['sha','expected_sha'],['extract','expected_extract']])if(u.searchParams.has(a))q.set(b,u.searchParams.get(a));
  const doc=await api('/citation/read?'+q);if(!doc.chunks.length)throw Error('原文片段不存在');
  evidence={...doc,passages:doc.chunks.map(c=>({...c,is_target:true}))};await renderEvidence(parts[0]);
  if(!u.searchParams.has('extract'))$('#evidence-status').textContent='旧式引用未记录提取批次，无法确认分段与当时一致；请核对原文。';
  return;
 }
 if(parts.length===1){await openDetail(parts[0]);return;}
 throw Error('引用地址无效');
}));

$('#copy-knowledge-config').onclick=()=>action(async()=>{
 const field=$('#knowledge-config');
 try{await navigator.clipboard.writeText(field.value);toast('MCP 配置已复制');}
 catch(e){field.focus();field.select();toast('请按 ⌘C 复制选中的配置');}
});

$('#pick-repository').onclick=()=>pickManagement('repository','#repository-path');
const resetRepositoryPlan=()=>{$('#repository-start').disabled=true;$('#repository-summary').textContent='';};
$('#repository-path').oninput=resetRepositoryPlan;$('#repository-external').onchange=resetRepositoryPlan;
window.addEventListener('native-selection',e=>{if(e.detail.kind==='repository'){$('#repository-path').value=e.detail.paths[0]||'';resetRepositoryPlan();}});
$('#repository-plan').onclick=()=>action(async()=>{
 const r=await api('/repository/plan',{path:$('#repository-path').value,include_external:$('#repository-external').checked});
 $('#repository-summary').textContent=`${r.objects} 个对象 · ${size(r.total_bytes)}。预计新增 ${size(r.new_bytes_estimate)} + 数据库快照，复用 ${size(r.reuse_bytes_estimate)}；可用 ${size(r.free_bytes)}。${r.same_volume?'当前目标与资料库在同一卷，不能防范此磁盘损坏。':'目标位于另一卷，请确认是独立备份介质。'} ${r.notice}`;
 $('#repository-start').disabled=r.free_bytes<r.new_bytes_estimate+r.database_bytes+32*1024*1024;
});
$('#repository-start').onclick=()=>action(async()=>{await api('/repository/backup',{path:$('#repository-path').value,include_external:$('#repository-external').checked});$('#backup-dialog').close();showDialog('#tasks-dialog');await refreshStatus();toast('增量备份已加入队列');});
$('#repository-list-button').onclick=()=>action(async()=>{const list=await api('/repository/list?path='+encodeURIComponent($('#repository-path').value));$('#repository-list').innerHTML=list.length?list.map(r=>`<div class="storage-item"><span>${date(r.created_at)} · ${r.objects} 个对象 · ${r.external_objects} 个外部对象未保护</span><button class="text-button" data-repository-restore="${esc(r.path)}">校验并恢复</button></div>`).join(''):'<p class="muted">暂无已完成快照</p>';});
$('#repository-list').onclick=e=>{const b=e.target.closest('[data-repository-restore]');if(b)action(()=>restoreBackup(b.dataset.repositoryRestore));};
