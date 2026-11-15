'use strict';
let classificationRules=[],editingRule=null,classificationPreview=null,preferenceSource=null,preferenceTargets=[];
const splitTags=value=>[...new Set(value.split(/[,，]/).map(x=>x.trim()).filter(Boolean))];
const conditionLabels={path_contains:'路径包含',path_prefix:'目录前缀',name_contains:'文件名包含',extensions:'扩展名',kind:'文件类型',size_min:'最小字节数',size_max:'最大字节数',modified_after:'修改日期起（UTC）',modified_before:'修改日期止（UTC）'};
const describeConditions=conditions=>Object.entries(conditions).map(([key,value])=>`${conditionLabels[key]||key}：${Array.isArray(value)?value.join('、'):value}`).join('；');
function collectionOptions(empty){return `<option value="">${empty}</option>`+(state.status?.collections||[]).map(c=>`<option value="${esc(c.id)}">${esc(c.path_label||c.name)}</option>`).join('');}
async function loadClassificationRules(){
 classificationRules=(await api('/classification/rules')).rules;
 $('#classification-rules-list').innerHTML=classificationRules.length?classificationRules.map(r=>`<article class="organization-card"><strong>${esc(r.name)}</strong><small>${r.enabled?'启用':'停用'} · 第 ${r.version} 版</small><p>${esc(describeConditions(r.conditions))}</p><p>${esc(r.collection_path?.join(' / ')||(r.target_missing?'目标分类已删除':'仅添加标签'))}${r.tags.length?' · '+esc(r.tags.join('，')):''}</p><button class="text-button" data-edit-rule="${esc(r.id)}">编辑</button><button class="text-button danger" data-delete-rule="${esc(r.id)}">删除规则</button></article>`).join(''):'<p class="muted">还没有规则。先选择已有分类，再设置匹配条件。</p>';
 const batches=(await api('/classification/batches')).batches;
 $('#classification-batches-list').innerHTML=batches.length?batches.map(b=>`<article class="organization-card"><strong>${esc(date(b.created_at))}</strong><p>${b.files} 份已归类 · ${b.undone} 份已撤销</p><button class="text-button" data-view-classification="${esc(b.id)}">查看归类结果</button><div data-batch-detail="${esc(b.id)}"></div><button class="secondary" data-undo-classification="${esc(b.id)}" ${b.files===b.undone?'disabled':''}>撤销本批新增归属</button></article>`).join(''):'<p class="muted">暂无已提交批次</p>';
}
function editClassificationRule(rule=null){
 editingRule=rule;$('#classification-rule-heading').textContent=rule?'编辑规则':'新建规则';
 $('#rule-collection').innerHTML=collectionOptions('不添加分类');
 const c=rule?.conditions||{};
 for(const [id,key] of [['path','path_contains'],['filename','name_contains'],['prefix','path_prefix'],['kind','kind'],['size-min','size_min'],['size-max','size_max'],['after','modified_after'],['before','modified_before']])$('#rule-'+id).value=c[key]??'';
 $('#rule-name').value=rule?.name||'';$('#rule-extensions').value=(c.extensions||[]).join(', ');
 $('#rule-collection').value=rule?.collection_id||'';$('#rule-tags').value=(rule?.tags||[]).join('，');
 $('#rule-priority').value=rule?.priority||0;$('#rule-enabled').checked=rule?.enabled??true;$('#rule-save-state').textContent='';
 if(rule?.target_missing)$('#rule-save-state').textContent='原目标分类已删除，请重新选择。';
}
const openClassificationRules=()=>action(async()=>{await refreshStatus();await loadClassificationRules();editClassificationRule();showDialog('#classification-rules-dialog');});
$('#classification-rules-open').onclick=openClassificationRules;
$('#settings-classification').onclick=openClassificationRules;
$('#classification-rule-new').onclick=()=>{editClassificationRule();$('#rule-name').focus();};
$('#classification-rules-list').onclick=e=>action(async()=>{
 const edit=e.target.closest('[data-edit-rule]'),remove=e.target.closest('[data-delete-rule]');
 if(edit){editClassificationRule(classificationRules.find(r=>r.id===edit.dataset.editRule));$('#rule-name').focus();}
 if(remove){const r=classificationRules.find(r=>r.id===remove.dataset.deleteRule);await api('/classification/delete',{id:r.id,expected_version:r.version});if(editingRule?.id===r.id)editClassificationRule();await loadClassificationRules();toast('规则已删除，已有分类保持原样');}
});
$('#classification-rule-form').onsubmit=e=>{e.preventDefault();action(async()=>{
 const conditions={};for(const [id,key] of [['path','path_contains'],['filename','name_contains'],['prefix','path_prefix'],['kind','kind'],['after','modified_after'],['before','modified_before']]){const value=$('#rule-'+id).value.trim();if(value)conditions[key]=value;}
 const extensions=splitTags($('#rule-extensions').value);if(extensions.length)conditions.extensions=extensions.map(v=>v.startsWith('.')?v:'.'+v);
 for(const [id,key] of [['size-min','size_min'],['size-max','size_max']])if($('#rule-'+id).value!=='')conditions[key]=Number($('#rule-'+id).value);
 const payload={name:$('#rule-name').value.trim(),conditions,collection_id:$('#rule-collection').value||null,tags:splitTags($('#rule-tags').value),priority:Number($('#rule-priority').value),enabled:$('#rule-enabled').checked};
 if(editingRule){payload.id=editingRule.id;payload.expected_version=editingRule.version;}
 const saved=await api('/classification/rule',payload);await loadClassificationRules();editClassificationRule(saved);$('#rule-save-state').textContent='已保存。回到候选列表，预览后才会应用。';toast('分类规则已保存');
 });};
$('#classification-batches-list').onclick=e=>{const view=e.target.closest('[data-view-classification]');if(view){action(async()=>{const result=await api('/classification/batch?id='+encodeURIComponent(view.dataset.viewClassification));$(`[data-batch-detail="${view.dataset.viewClassification}"]`).innerHTML=result.files.map(f=>`<p class="note">${esc(f.name||f.file_id)} · 新增分类：${esc(f.added_collections.map(c=>c.path_at_preview.join(' / ')).join('；')||'无')} · 新增标签：${esc(f.added_tags.join('，')||'无')} · ${f.undone_at?'已撤销':f.unchanged_since_apply?'归属已核对':'后续已变化'}</p>`).join('')||'<p class="note">任务尚无成功归类记录，请查看任务状态。</p>';});return;}const b=e.target.closest('[data-undo-classification]');if(!b)return;action(async()=>{
 b.disabled=true;try{const result=await api('/classification/undo',{batch_id:b.dataset.undoClassification});await refreshStatus();await loadClassificationRules();$('#classification-undo-result').textContent=`已撤销 ${result.undone.length} 份；跳过 ${result.conflicts.length} 份。${result.conflicts.map(x=>x.file_id+'：'+x.reason).join('；')}`;}finally{b.disabled=false;}
});};
$('#discovery-import').onclick=()=>action(async()=>{
 $('#discovery-import').disabled=true;
 try{const request={id:discovery.id,ids:[...discovery.selected],mode:$('#discovery-mode').value};const preview=await api('/discovery/preview',{id:request.id,ids:request.ids});classificationPreview={...request,preview,request_id:'classification-'+preview.plan_id};
 $('#classification-preview-summary').textContent=`已选 ${preview.selected} 份，${preview.matched} 份匹配规则。${preview.notice}`;
 $('#classification-preview-items').innerHTML=preview.items.map(item=>`<article class="organization-card"><strong>${esc(item.name)}</strong><small>${esc(item.path)}</small><p>${item.collections.length?esc(item.collections.map(c=>c.path.join(' / ')).join('；')):'不添加分类'}${item.tags.length?' · '+esc(item.tags.join('，')):''}</p>${item.rules.map(r=>`<p class="note">${esc(r.name)}（第 ${r.version} 版）：${esc(describeConditions(r.conditions))}</p>`).join('')}${item.conflicts.map(c=>`<p class="notice">${esc(c)}</p>`).join('')}</article>`).join('');
 $('#classification-confirm').disabled=!preview.can_apply;showDialog('#classification-preview-dialog');
 }finally{discoverySelection();}
});
$('#classification-confirm').onclick=()=>action(async()=>{
 if(!classificationPreview?.preview.can_apply)return;
 const p=classificationPreview;$('#classification-confirm').disabled=true;
 try{await api('/discovery/import',{id:p.id,ids:p.ids,mode:p.mode,request_id:p.request_id,plan_id:p.preview.plan_id});discovery.selected.clear();classificationPreview=null;$('#classification-preview-dialog').close();$('#discovery-dialog').close();showDialog('#tasks-dialog');await refreshStatus();toast('已提交入库与分类，可在分类批次中核对');}
 finally{$('#classification-confirm').disabled=false;discoverySelection();}
});
function preferencePanel(file){const p=file.preference;return `<div class="detail-section"><h3>优先条目</h3><p class="note">${p?esc(p.preferred_name)+' · '+esc(p.reason)+(p.active?'':'（文件版本已变化，需重新确认）'):'可明确关联已校对的成品或修订版。'}</p><button id="edit-preference" class="secondary">${p?'管理关联':'指定优先条目'}</button></div>`;}
async function openPreference(file){preferenceSource=file;preferenceTargets=[];$('#preference-source').textContent=file.name+' · '+file.origin_path;$('#preference-query').value=file.name;$('#preference-target').innerHTML='<option value="">请查找并核对文件位置</option>';$('#preference-reason').value=file.preference?.reason||'';$('#preference-clear').disabled=!file.preference;showDialog('#preference-dialog');}
$('#preference-search-form').onsubmit=e=>{e.preventDefault();action(async()=>{
 const result=await api('/files?'+new URLSearchParams({q:$('#preference-query').value}));preferenceTargets=result.files.filter(f=>f.id!==preferenceSource.id);
 $('#preference-target').innerHTML='<option value="">请选择（同名文件请核对路径）</option>'+preferenceTargets.map(f=>`<option value="${esc(f.id)}">${esc(f.name)} · ${esc(f.origin_path)}</option>`).join('');
 if(!preferenceTargets.length)toast('没有其他候选，请调整文件名');else if(result.total>result.files.length)toast('候选较多，请缩小搜索词');
 });};
async function savePreference(clear){
 const target=clear?null:preferenceTargets.find(f=>f.id===$('#preference-target').value);if(!clear&&!target)throw Error('请先选择并核对优先条目');
 await api('/files/preference',{file_id:preferenceSource.id,preferred_id:target?.id||null,expected_sha:preferenceSource.sha,target_sha:target?.sha||null,reason:$('#preference-reason').value.trim()});
 $('#preference-dialog').close();await openDetail(preferenceSource.id);toast(clear?'关联已取消':'优先条目已保存');
}
$('#preference-save').onclick=()=>action(()=>savePreference(false));$('#preference-clear').onclick=()=>action(()=>savePreference(true));
