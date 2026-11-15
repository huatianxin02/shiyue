'use strict';
const discovery={id:null,state:null,items:[],selected:new Set(),offset:0,more:false,timer:null,seq:0,requestId:null};
function discoverySelection(){
 $('#discovery-selected').textContent=`已选 ${discovery.selected.size} 个（最多 1000 个）`;
 $('#discovery-import').disabled=!discovery.selected.size||discovery.state==='running';
 const eligible=discovery.items.filter(x=>!x.indexed);
 $('#discovery-select-page').checked=!!eligible.length&&eligible.every(x=>discovery.selected.has(x.id));
 $('#discovery-select-page').indeterminate=eligible.some(x=>discovery.selected.has(x.id))&&!$('#discovery-select-page').checked;
}
async function openDiscovery(){
 $('#import-dialog').close();showDialog('#discovery-dialog');
 const result=await api('/discovery/locations');
 if(!$('#discovery-locations').children.length)$('#discovery-locations').innerHTML=result.locations.map(x=>`<label><input type="checkbox" data-discovery-root="${esc(x.path)}" ${(result.defaults||[]).includes(x.path)?'checked':''}><span>${esc(x.name)}<small>${esc(x.path)}</small></span></label>`).join('');
 if(discovery.id)await refreshDiscovery();
}
function stopDiscoveryPoll(){clearTimeout(discovery.timer);discovery.timer=null;}
async function refreshDiscovery(){
 stopDiscoveryPoll();if(!discovery.id)return;
 const seq=++discovery.seq,id=discovery.id;
 try{
 const params=new URLSearchParams({id,q:$('#discovery-query').value,kind:$('#discovery-kind').value,offset:String(discovery.offset),limit:'100'});
 const r=await api('/discovery/scan?'+params);
 if(seq!==discovery.seq||id!==discovery.id)return;
 discovery.items=r.items;discovery.more=r.has_more;discovery.state=r.state;
 const labels={running:'正在搜索',done:'搜索完成',cancelled:'已停止搜索',limited:'已达到搜索上限，请缩小范围继续',failed:'搜索失败'};
 $('#discovery-status').textContent=`${labels[r.state]||r.state} · 找到 ${r.total??r.found??r.count??r.filtered_total} 个 · 当前筛选 ${r.filtered_total} 个${r.errors?' · 无法访问 '+r.errors+' 处':''}${r.limited_reason?' · '+r.limited_reason:''}${r.error?' · '+r.error:''}`;
 $('#discovery-cancel').disabled=r.state!=='running';$('#discovery-start').disabled=r.state==='running';
 $('#discovery-results').innerHTML=r.items.length?r.items.map(x=>`<label class="discovery-item"><input type="checkbox" data-candidate="${esc(x.id)}" ${x.indexed?'disabled':''} ${discovery.selected.has(x.id)?'checked':''}><span><strong>${esc(x.name)}</strong><small>${esc(x.path)}</small></span><em>${size(x.size)}${x.indexed?' · 已入库':''}</em></label>`).join(''):'<p class="muted">暂无候选文件，可调整范围或筛选。</p>';
 $('#discovery-page').textContent=r.filtered_total?`${discovery.offset+1}–${discovery.offset+r.items.length} / ${r.filtered_total}`:'0';
 $('#discovery-prev').disabled=!discovery.offset;$('#discovery-next').disabled=!r.has_more;discoverySelection();
 if(r.state==='running'&&$('#discovery-dialog').open)discovery.timer=setTimeout(()=>action(refreshDiscovery),900);
 }catch(e){$('#discovery-start').disabled=false;$('#discovery-status').textContent=e.message;throw e;}
}
$('#discover-button').onclick=$('#discover-from-import').onclick=()=>action(openDiscovery);
$('#discovery-dialog').addEventListener('close',()=>{stopDiscoveryPoll();++discovery.seq;});
$('#discovery-pick').onclick=()=>{if(window.webkit?.messageHandlers?.native)window.webkit.messageHandlers.native.postMessage({action:'pick',kind:'discovery'});else{$('#discovery-paths').focus();toast('请粘贴本机目录或磁盘路径');}};
window.addEventListener('native-selection',e=>{if(e.detail.kind==='discovery')$('#discovery-paths').value=[...new Set([...$('#discovery-paths').value.split('\n'),...e.detail.paths].filter(Boolean))].join('\n');});
$('#discovery-start').onclick=()=>action(async()=>{
 const paths=[...new Set([...$$('[data-discovery-root]:checked').map(x=>x.dataset.discoveryRoot),...$('#discovery-paths').value.split('\n').map(s=>s.trim()).filter(Boolean)])];
 $('#discovery-start').disabled=true;
 try{const r=await api('/discovery/start',{paths});discovery.id=r.id;discovery.offset=0;discovery.selected.clear();discovery.requestId=null;await refreshDiscovery();}catch(e){$('#discovery-start').disabled=false;throw e;}
});
$('#discovery-cancel').onclick=()=>action(async()=>{await api('/discovery/cancel',{id:discovery.id});await refreshDiscovery();});
let discoveryFilterTimer;
$('#discovery-query').oninput=()=>{clearTimeout(discoveryFilterTimer);discoveryFilterTimer=setTimeout(()=>{discovery.offset=0;action(refreshDiscovery);},250);};
$('#discovery-kind').onchange=()=>{discovery.offset=0;action(refreshDiscovery);};
$('#discovery-prev').onclick=()=>{discovery.offset=Math.max(0,discovery.offset-100);action(refreshDiscovery);};
$('#discovery-next').onclick=()=>{discovery.offset+=100;action(refreshDiscovery);};
$('#discovery-results').onchange=e=>{const id=e.target.dataset.candidate;if(!id)return;if(e.target.checked&&discovery.selected.size>=1000){e.target.checked=false;toast('每批最多选择 1000 个文件',true);return;}e.target.checked?discovery.selected.add(id):discovery.selected.delete(id);discovery.requestId=null;discoverySelection();};
$('#discovery-select-page').onchange=e=>{for(const x of discovery.items.filter(x=>!x.indexed)){if(e.target.checked){if(discovery.selected.size<1000)discovery.selected.add(x.id);}else discovery.selected.delete(x.id);}discovery.requestId=null;$$('[data-candidate]').forEach(x=>x.checked=discovery.selected.has(x.dataset.candidate));discoverySelection();};
$('#discovery-mode').onchange=()=>discovery.requestId=null;
$('#discovery-import').onclick=()=>action(async()=>{
 $('#discovery-import').disabled=true;
 discovery.requestId ||= 'discovery-'+(window.crypto?.randomUUID?.()||Date.now()+'-'+Math.random().toString(36).slice(2));
 try{await api('/discovery/import',{id:discovery.id,ids:[...discovery.selected],mode:$('#discovery-mode').value,request_id:discovery.requestId});discovery.selected.clear();$('#discovery-dialog').close();showDialog('#tasks-dialog');await refreshStatus();toast('所选文件已加入索引队列');}finally{discoverySelection();}
});
$('#load-ingest-command').onclick=()=>action(async()=>{const c=await api('/ingest/config');const quote=s=>"'"+s.replaceAll("'","'\\''")+"'";$('#ingest-command').value=[quote(c.command),'-B',quote(c.script),'--data',quote(c.data),'--request-id',quote('任务编号-成果版本'),'--wait','120',quote('/输出文件的绝对路径')].join(' ');});
$('#copy-ingest-command').onclick=()=>action(async()=>{if(!$('#ingest-command').value)throw Error('请先生成本机调用命令');await navigator.clipboard.writeText($('#ingest-command').value);toast('调用命令已复制');});
