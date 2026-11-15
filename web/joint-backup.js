/* Local joint backup and restoration review. */
(() => {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
  const bytes = value => value < 1048576 ? (value / 1024).toFixed(1) + ' KB' : value < 1073741824 ? (value / 1048576).toFixed(1) + ' MB' : (value / 1073741824).toFixed(2) + ' GB';
  function initialize() {
    const root = document.querySelector('#joint-backup');
    if (!root) return;
    root.style.minWidth = '0';
    root.style.overflowWrap = 'anywhere';
    const app = document.querySelector('meta[name="integration-app"]')?.content;
    if (app === 'vault') {
      root.innerHTML = '<h2>三端联合备份</h2><p>在小鲸控制台预览备份范围、保管成果并检查恢复副本。</p><button id="joint-open-console">打开小鲸控制台</button><p id="joint-open-message" role="status"></p>';
      root.querySelector('button').onclick = async () => {
        try {await globalThis.LocalCollaboration.api('open-console', {});}
        catch (error) {root.querySelector('#joint-open-message').textContent = error.message;}
      };
      return;
    }
    root.innerHTML = `
      <div class="section-head"><h2>三端联合备份</h2><p>保管任务记录、登记成果与拾页资料，恢复到新的独立目录进行检查。</p></div>
      <form id="joint-plan-form"><label for="joint-destination">备份到新的文件夹</label><input id="joint-destination" required placeholder="/Volumes/备份盘/三端备份-日期">
        <label><input id="joint-external" type="checkbox"> 同时复制拾页的外部原件</label><p class="fine">外部原件可能占用较多空间。先预览数量、磁盘空间和无法包含的文件。</p><button type="submit">预览备份范围</button></form>
      <div id="joint-plan" aria-live="polite"></div><button id="joint-create" class="primary" disabled>确认范围并创建备份</button>
      <div class="section-head"><h3>检查或恢复已有备份</h3></div>
      <label for="joint-source">联合备份文件夹</label><input id="joint-source" placeholder="包含 manifest.json 的备份文件夹">
      <label for="joint-restore-destination">恢复到新的文件夹</label><input id="joint-restore-destination" placeholder="/Users/你的用户名/Documents/三端恢复副本">
      <p class="fine">恢复副本保持任务暂停和桥接待核对。个人数据、文件引用和哈希会检查后列入报告。</p>
      <div class="actions"><button id="joint-check">校验备份</button><button id="joint-restore">恢复并检查新副本</button><button id="joint-refresh">刷新记录</button></div>
      <p id="joint-message" role="status" aria-live="polite"></p><div id="joint-jobs" class="catalog"></div>`;
    let plan = null;
    let polling = null;
    const status = value => {root.querySelector('#joint-message').textContent = value;};
    async function api(action, data) {
      if (globalThis.LocalCollaboration?.api) return globalThis.LocalCollaboration.api('joint-backup/' + action, data);
      const response = await fetch('/api/bridge/joint-backup/' + action, {
        method: data === undefined ? 'GET' : 'POST',
        headers: {'X-Console-Token':sessionStorage.getItem('xj-token'), ...(data === undefined ? {} : {'Content-Type':'application/json'})},
        body: data === undefined ? undefined : JSON.stringify(data),
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || '联合备份操作未完成');
      return result;
    }
    const labels = {queued:'等待开始',running:'处理中',done:'检查完成',partial:'处理完成，资料覆盖需核对',failed:'处理失败',interrupted:'已中断，请核对目标'};
    async function refresh() {
      try {
        const result = await api('jobs');
        root.querySelector('#joint-jobs').innerHTML = result.items.map(job => `<article class="card"><h3>${escape({create:'创建备份',check:'校验备份',restore:'恢复新副本'}[job.operation] || job.operation)}</h3><span class="badge">${escape(labels[job.state] || job.state)}</span><p>${job.total ? job.done + ' / ' + job.total : ''}</p>${job.destination || job.source ? '<p class="meta">' + escape(job.destination || job.source) + '</p>' : ''}${job.error ? '<p class="warn-text">' + escape(job.error) + '</p>' : ''}${job.result ? '<p class="meta">' + escape(job.result.path || job.result.destination || '') + '</p><p>' + (job.result.coverage_complete ? '已包含登记资料，引用检查通过' : '有未包含原件或历史版本，请查看备份清单或恢复检查报告') + '</p>' : ''}</article>`).join('') || '<p class="empty">还没有联合备份记录。</p>';
        root.querySelector('#joint-create').disabled = !plan || result.busy;
        if (!result.busy && polling) {clearInterval(polling);polling = null;}
        if (result.busy && !polling) polling = setInterval(refresh, 2500);
      } catch (error) {status(error.message);}
    }
    async function start(action, payload) {
      status('操作已提交，正在记录进度…');
      await api(action, {...payload, confirmed:true});
      await refresh();
    }
    root.querySelector('#joint-plan-form').onsubmit = async event => {
      event.preventDefault();
      plan = null;
      root.querySelector('#joint-create').disabled = true;
      status('正在核对来源与磁盘空间…');
      try {
        plan = await api('plan', {destination:root.querySelector('#joint-destination').value, include_external:root.querySelector('#joint-external').checked});
        root.querySelector('#joint-plan').innerHTML = '<article class="card"><h3>备份范围</h3><p>数据库 ' + plan.databases.length + ' 份 · 预计 ' + bytes(plan.total_bytes_estimate) + ' · 临时空间预计 ' + bytes(plan.peak_bytes_estimate) + ' · 磁盘可用 ' + bytes(plan.free_bytes) + '</p>' + Object.entries(plan.categories).map(([name,item]) => '<p>' + escape({workbench:'任务与记录文件',console:'小鲸已选成果',vault:'拾页元数据',workspace:'登记工作区成果',managed:'拾页托管对象',external:'外部原件'}[name] || name) + ' · ' + item.files + ' 份 · ' + bytes(item.bytes) + '</p>').join('') + '<p>' + escape(plan.coverage_notice) + '</p><p class="warn-text">本次未包含 ' + plan.excluded_count + ' 项原件或登记文件</p>' + plan.exclusions.slice(0,10).map(item => '<p class="fine">' + escape(item.path || item.sha256 || '') + ' · ' + escape(item.reason) + '</p>').join('') + '</article>';
        status('请核对范围后创建备份。');
        await refresh();
      } catch (error) {status(error.message);}
    };
    for (const id of ['joint-destination','joint-external']) root.querySelector('#' + id).addEventListener('input', () => {plan = null;root.querySelector('#joint-create').disabled = true;});
    root.querySelector('#joint-create').onclick = async () => {
      if (!plan || !confirm('按已预览范围创建联合备份？\n' + plan.destination + '\n预计 ' + bytes(plan.total_bytes_estimate))) return;
      try {await start('create',{plan_id:plan.id});plan = null;}
      catch (error) {status(error.message);}
    };
    root.querySelector('#joint-check').onclick = async () => {
      const path = root.querySelector('#joint-source').value;
      if (!path || !confirm('校验这份联合备份的数据库和全部文件哈希？')) return;
      try {await start('check',{path});} catch (error) {status(error.message);}
    };
    root.querySelector('#joint-restore').onclick = async () => {
      const path = root.querySelector('#joint-source').value;
      const destination = root.querySelector('#joint-restore-destination').value;
      if (!path || !destination || !confirm('恢复到这个新的独立目录，并保持任务暂停、连接待核对？\n' + destination)) return;
      try {await start('restore',{path,destination});} catch (error) {status(error.message);}
    };
    root.querySelector('#joint-refresh').onclick = refresh;
    refresh();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initialize);
  else initialize();
})();
