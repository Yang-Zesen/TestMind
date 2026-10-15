const project = () => el('user').value === 'demo-user' ? 'demo-project' : 'other-project';
const kb = () => el('user').value === 'demo-user' ? 'demo-kb' : 'other-kb';
let sourceEdit = null, caseEdit = null;
function text(tag, value) { const node = document.createElement(tag); node.textContent = value; return node; }
function command(label, handler, disabled = false) {
  const button = text('button', label); button.type = 'button'; button.disabled = disabled;
  button.onclick = async () => { button.disabled = true; try { await handler(); } catch (e) { fail(e); } finally { button.disabled = disabled; } };
  return button;
}
async function uploadKnowledge(file, documentId = null) {
  if (!file) throw Error('请选择资料');
  const user = el('user').value;
  const suffix = file.name.split('.').pop().toLowerCase();
  const type = {pdf:'application/pdf',md:'text/markdown',markdown:'text/markdown',txt:'text/plain'}[suffix];
  const form = new FormData(); form.append('file', new Blob([file], {type}), file.name);
  const url = documentId ? `/api/v1/documents/${documentId}/versions` : `/api/v1/knowledge-bases/${kb()}/documents`;
  const response = await fetch(url, {method:'POST', headers:{'X-Demo-User':user,'Idempotency-Key':crypto.randomUUID()}, body:form});
  if (!response.ok) throw Error(JSON.stringify(await response.json()));
  const accepted = await response.json(); if (user !== el('user').value) return;
  el('knowledge-status').textContent = `入库任务 ${accepted.job_id.slice(0,8)} 已登记`;
  await refreshKnowledge();
}
async function showEvidence(citation) {
  const user = el('user').value;
  const evidence = await api(`/api/v1/knowledge-chunks/${encodeURIComponent(citation.chunk_id)}?project_id=${encodeURIComponent(project())}`);
  if (user !== el('user').value) return;
  el('evidence-title').textContent = `${evidence.title} · ${evidence.document_version}`;
  el('evidence-content').textContent = `${evidence.source_locator}\n\n${evidence.content}`;
  el('evidence-dialog').showModal();
}
const originalDetail = detail;
detail = async () => {
  const task = await originalDetail(); if (!task) return;
  let container = el('citations'); if (!container) { container = document.createElement('div'); container.id = 'citations'; el('answer').after(container); }
  container.replaceChildren();
  for (const citation of task.result.citations || []) container.append(command(`${citation.title || '来源'} · ${citation.document_version}`, () => showEvidence(citation)));
  if (task.result.case_asset_ids?.length) await refreshCases();
  else { el('case-section').hidden = true; el('case-list').replaceChildren(); }
  if (task.result.graph_result) renderGraph(task.result.graph_result);
  else { graphCursor=null; el('graph-output').replaceChildren(); }
  return task;
};
async function refreshKnowledge() {
  const user = el('user').value;
  const documents = await api(`/api/v1/knowledge-bases/${kb()}/documents`);
  if (user !== el('user').value) return;
  el('documents').replaceChildren();
  for (const doc of documents) {
    const row = document.createElement('tr'); row.append(text('td', doc.title), text('td', doc.active_version || '未激活'));
    const actions = document.createElement('td'); actions.append(command('版本 / 验收', async () => {
      const versions = await api(`/api/v1/documents/${doc.id}/versions`); if (user !== el('user').value) return;
      const panel = document.createElement('div');
      for (const version of versions) {
        const line = text('p', `${version.document_version} · ${version.status} `);
        line.append(command('质量报告', async () => { const report = await api(`/api/v1/versions/${version.id}/quality-report`); if(user !== el('user').value)return; el('evidence-title').textContent='质量报告';el('evidence-content').textContent=JSON.stringify(report,null,2);el('evidence-dialog').showModal(); }));
        line.append(command('索引报告', async () => { const report = await api(`/api/v1/versions/${version.id}/index-report`); if(user !== el('user').value)return; el('evidence-title').textContent='索引验收';el('evidence-content').textContent=JSON.stringify(report,null,2);el('evidence-dialog').showModal(); }));
        line.append(command('激活', async () => { await api(`/api/v1/documents/${doc.id}/versions/${version.id}/activate`, {method:'POST'}); el('knowledge-status').textContent='激活任务已登记';await refreshKnowledge(); }, version.status !== 'ready'));
        panel.append(line);
      }
      actions.append(panel);
    }));
    const replacement = document.createElement('input'); replacement.type = 'file'; replacement.accept = '.pdf,.md,.markdown,.txt'; replacement.hidden = true;
    replacement.onchange = async () => { try { if (user === el('user').value) await uploadKnowledge(replacement.files[0], doc.id); } catch (e) { fail(e); } finally { replacement.value = ''; } };
    actions.append(command('替换版本', async () => replacement.click()), replacement);
    actions.append(command('重建索引', async () => { await api(`/api/v1/documents/${doc.id}/reindex`,{method:'POST'});await refreshKnowledge(); }));
    actions.append(command('撤回', async () => { const preview=await api(`/api/v1/documents/${doc.id}/maintenance-preview`);if(user!==el('user').value)return;if(!confirm(`撤回“${doc.title}”？${preview.version_count} 个版本将停止参与问答。`))return;await api(`/api/v1/documents/${doc.id}`,{method:'DELETE'});await refreshKnowledge(); }));
    row.append(actions); el('documents').append(row);
  }
  const jobs = await api(`/api/v1/knowledge-bases/${kb()}/jobs`); if (user !== el('user').value) return;
  el('knowledge-jobs').replaceChildren(); for (const job of jobs.slice(0,6)) {
    const line=text('p',`${job.operation} · ${job.status} · ${job.id.slice(0,8)}`);
    if(job.status==='failed_retryable')line.append(command('重试',async()=>{await api(`/api/v1/jobs/${job.id}/retry`,{method:'POST'});await refreshKnowledge();}));
    el('knowledge-jobs').append(line);
  }
}
el('upload').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await uploadKnowledge(el('knowledge-file').files[0]);}catch(e){fail(e);}finally{button.disabled=false;}};
async function refreshSources(){const user=el('user').value,sources=await api(`/api/v1/case-sources?project_id=${project()}`);if(user!==el('user').value)return;el('sources').replaceChildren();for(const source of sources){const row=document.createElement('tr'),select=document.createElement('input'),cell=document.createElement('td');select.type='checkbox';select.value=source.id;select.setAttribute('aria-label',source.title);cell.append(select);row.append(cell,text('td',source.title),text('td',source.kind),text('td',String(source.version)));const actions=document.createElement('td');actions.append(command('编辑来源',async()=>{sourceEdit=source;el('source-kind').value=source.kind;el('source-kind').disabled=true;el('source-title').value=source.title;el('source-title').disabled=true;el('source-content').value=source.content;el('source-status').textContent=`编辑版本 ${source.version}`;el('source-form').scrollIntoView();}));actions.append(command('删除来源',async()=>{if(!confirm(`删除“${source.title}”？`))return;await api(`/api/v1/case-sources/${source.id}`,{method:'DELETE'});await refreshSources();}));row.append(actions);el('sources').append(row);}}
function newSource(){sourceEdit=null;el('source-form').reset();el('source-kind').disabled=false;el('source-title').disabled=false;el('source-status').textContent='';}
el('source-form').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{if(sourceEdit)await api(`/api/v1/case-sources/${sourceEdit.id}`,{method:'PATCH',body:JSON.stringify({version:sourceEdit.version,content:el('source-content').value})});else await api('/api/v1/case-sources',{method:'POST',body:JSON.stringify({project_id:project(),kind:el('source-kind').value,title:el('source-title').value,content:el('source-content').value})});newSource();el('source-status').textContent='来源已保存';await refreshSources();}catch(e){fail(e);}finally{button.disabled=false;}};
const knowledgeOption = document.createElement('input'); knowledgeOption.type = 'checkbox'; knowledgeOption.id = 'case-use-knowledge';
const knowledgeLabel = text('label', '引用知识库'); knowledgeLabel.htmlFor = knowledgeOption.id; knowledgeLabel.prepend(knowledgeOption); el('design-cases').before(knowledgeLabel);
el('design-cases').onclick=async()=>{try{const source_ids=Array.from(el('sources').querySelectorAll('input:checked')).map(input=>input.value);if(!source_ids.length)throw Error('请选择需求或 OpenAPI 来源');const task=await api('/api/v1/case-design',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({project_id:project(),source_ids,use_knowledge:knowledgeOption.checked,session_id:el('session').value})});await refresh();await openTask(task.task_id);}catch(e){fail(e);}};
async function refreshCases(){const id=current,user=el('user').value;if(!id)return;const assets=await api(`/api/v1/tasks/${id}/cases`);if(id!==current||user!==el('user').value)return;el('case-section').hidden=!assets.length;el('case-list').replaceChildren();for(const asset of assets){const item=document.createElement('article');item.className='case-item';item.append(text('h3',asset.body.title),text('small',`${asset.body.kind} · 版本 ${asset.version} · ${asset.decision}${asset.source_stale?' · 来源已变化':''}`),text('p',`前置：${asset.body.preconditions}\n步骤：${asset.body.steps.join('\n')}\n预期：${asset.body.expected}`),text('small',`覆盖：${asset.body.factors.join('；')} · 缺口：${asset.body.coverage_gaps.join('；')||'待人工复核'}`));const actions=document.createElement('div');actions.className='row';const payload={version:asset.version,content_hash:asset.content_hash};const published=asset.publications.some(op=>op.status==='completed'),locked=asset.publications.some(op=>['completed','in_flight','needs_reconciliation'].includes(op.status));actions.append(command('编辑',async()=>{caseEdit=asset;for(const [field,value]of Object.entries({'case-title':asset.body.title,'case-preconditions':asset.body.preconditions,'case-steps':asset.body.steps.join('\n'),'case-expected':asset.body.expected,'case-factors':asset.body.factors.join('\n')}))el(field).value=value;el('case-editor').showModal();},locked));for(const [decision,label]of [['approved','批准'],['rejected','拒绝']])actions.append(command(label,async()=>{await api(`/api/v1/test-cases/${asset.id}/review`,{method:'POST',body:JSON.stringify({...payload,decision})});await refreshCases();},asset.decision!=='pending'||asset.source_stale||locked));actions.append(command('发布到模拟禅道',async()=>{await api(`/api/v1/test-cases/${asset.id}/publish`,{method:'POST',body:JSON.stringify(payload)});await refreshCases();},asset.decision!=='approved'||asset.source_stale||locked));item.append(actions);for(const operation of asset.publications){const line=text('p',`${operation.status} · ${operation.external_id||operation.error_code||'处理中'} · 本地模拟`);if(['in_flight','needs_reconciliation'].includes(operation.status))line.append(command('核实结果',async()=>{await api(`/api/v1/case-publications/${operation.id}/reconcile`,{method:'POST'});await refreshCases();}));item.append(line);}if(published)item.append(text('small','已保存模拟禅道 ID'));el('case-list').append(item);}}
el('case-edit-form').onsubmit=async event=>{event.preventDefault();try{const body={...caseEdit.body,title:el('case-title').value,preconditions:el('case-preconditions').value,steps:el('case-steps').value.split('\n').filter(value=>value.trim()),expected:el('case-expected').value,factors:el('case-factors').value.split('\n').filter(value=>value.trim())};await api(`/api/v1/test-cases/${caseEdit.id}`,{method:'PATCH',body:JSON.stringify({version:caseEdit.version,body})});el('case-editor').close();await refreshCases();}catch(e){fail(e);}};
el('editor-close').onclick=()=>el('case-editor').close();el('evidence-close').onclick=()=>el('evidence-dialog').close();el('knowledge-refresh').onclick=()=>refreshKnowledge().catch(fail);el('source-refresh').onclick=()=>refreshSources().catch(fail);el('source-new').onclick=newSource;el('case-refresh').onclick=()=>refreshCases().catch(fail);
el('user').addEventListener('change',()=>{newSource();caseEdit=null;el('case-editor').close();el('evidence-dialog').close();el('case-list').replaceChildren();el('case-section').hidden=true;el('documents').replaceChildren();el('sources').replaceChildren();el('knowledge-jobs').replaceChildren();el('knowledge-status').textContent='';el('citations')?.replaceChildren();refreshKnowledge().catch(fail);refreshSources().catch(fail);});
refreshKnowledge().catch(fail);refreshSources().catch(fail);

let graphCursor = null, graphJobs = [], graphStatusVersion = 0;
function renderGraph(result) {
  el('graph-output').replaceChildren();
  graphCursor = result.next_cursor || null;
  if (result.items) {
    const scroll=document.createElement('div'); scroll.className='scroll';
    const table=document.createElement('table'),head=document.createElement('thead'),header=document.createElement('tr');
    for(const label of ['来源','类型','标题','版本','关系'])header.append(text('th',label));head.append(header);table.append(head);
    for(const item of result.items){const row=document.createElement('tr');let properties={};try{properties=JSON.parse(item.properties_json||'{}');}catch{}for(const value of [item.source_instance,item.entity_type,properties.title||item.source_id,item.revision,item.kind?`${item.kind} → ${item.target_title||item.target_source_id||item.target_key}`:''])row.append(text('td',String(value)));table.append(row);}
    scroll.append(table);el('graph-output').append(scroll);
    if(graphCursor)el('graph-output').append(command('下一页',()=>submitGraph(graphCursor)));
    el('graph-status').textContent=`${result.items.length} 条 · ${result.stale?'投影有偏差':'当前授权范围'} · 权限版本 ${result.permission_version}`;
  } else {
    const row=document.createElement('div');row.className='row';for(const [key,label]of [['requirements','需求'],['test_cases','用例'],['bugs','缺陷'],['automated_cases','自动化用例']])row.append(text('p',`${label}：${result[key]??0}`));el('graph-output').append(row);
    el('graph-status').textContent='当前资产快照 · 无历史趋势';
  }
  if(result.provenance)el('graph-output').append(text('small',`来源登记时间：${result.provenance.source_updated_at||'暂无'} · 代际：${result.provenance.generation}`));
}
async function submitGraph(cursor=null){const task=await api('/api/v1/tasks',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({project_id:project(),session_id:el('session').value,query:'graph-query:'+JSON.stringify({query_type:el('graph-mode').value,entity_type:el('graph-type').value||null,limit:20,cursor})})});await refresh();await openTask(task.task_id);}
el('graph-query-form').onsubmit=event=>{event.preventDefault();submitGraph().catch(fail);};
async function refreshGraphAccess(){const user=el('user').value;const projects=await api('/api/v1/graph/projects');if(user!==el('user').value)return;el('graph-maintenance').hidden=!projects.find(item=>item.project_id===project())?.can_maintain;}
async function refreshGraphStatus(){const user=el('user').value,version=++graphStatusVersion,jobs=[...graphJobs];const status=await api(`/api/v1/graph/sync-status?project_id=${project()}`);const fragment=document.createDocumentFragment();for(const job of jobs){const saved=await api(`/api/v1/integrations/sync-jobs/${job.job_id}`);if(user!==el('user').value||version!==graphStatusVersion)return;fragment.append(text('p',`${job.operation} · ${saved.status} · ${saved.error_code||saved.id.slice(0,8)}`));}if(user!==el('user').value||version!==graphStatusVersion)return;el('graph-status').textContent=`待处理 ${status.health.pending} · 最近投影 ${status.health.last_projected_at||'暂无'}`;if(status.provenance.watermarks.length)fragment.append(text('small',status.provenance.watermarks.map(item=>`${item.resource_type} 水位 ${JSON.stringify(item.cursor)}`).join(' · ')));el('graph-jobs').replaceChildren(fragment);}
async function graphMaintenance(operation){const user=el('user').value;if(operation==='rebuild'&&!confirm('重建独立代际，校验通过后切换查询投影？'))return;if(operation==='pull'){for(const resource_type of ['project','member','requirement','test_case','bug']){if(user!==el('user').value)return;graphJobs.push({...await api('/api/v1/integrations/sync-runs',{method:'POST',body:JSON.stringify({project_id:project(),resource_type})}),operation:`同步 ${resource_type}`});}}else graphJobs.push({...await api(`/api/v1/graph/${operation}`,{method:'POST',body:JSON.stringify({project_id:project()})}),operation});await refreshGraphStatus();}
for(const operation of ['pull','reconcile','rebuild'])el(`graph-${operation}`).onclick=async()=>{const buttons=['pull','reconcile','rebuild'].map(name=>el(`graph-${name}`));buttons.forEach(button=>button.disabled=true);try{await graphMaintenance(operation);}catch(e){fail(e);}finally{buttons.forEach(button=>button.disabled=false);}};
el('graph-sync-refresh').onclick=()=>refreshGraphStatus().catch(fail);
el('graph-cleanup-preview').onclick=async()=>{try{const user=el('user').value;const preview=await api(`/api/v1/graph/maintenance-preview?project_id=${project()}`);if(user!==el('user').value)return;el('evidence-title').textContent='旧代际清理预览';el('evidence-content').textContent=JSON.stringify(preview,null,2);el('evidence-dialog').showModal();}catch(e){fail(e);}};
el('user').addEventListener('change',()=>{graphCursor=null;graphJobs=[];el('graph-output').replaceChildren();el('graph-jobs').replaceChildren();el('graph-status').textContent='';el('graph-maintenance').hidden=true;refreshGraphAccess().catch(fail);});
refreshGraphAccess().catch(fail);

const runtimeSection = document.createElement('section');
runtimeSection.id = 'runtime-section'; runtimeSection.hidden = true;
runtimeSection.append(text('h2', '运行状态'));
const runtimeOutput = document.createElement('div'); runtimeOutput.id = 'runtime-output';
runtimeSection.append(command('刷新运行状态', async () => {
  const user = el('user').value;
  const status = await api(`/api/v1/operations?project_id=${project()}`);
  if (user !== el('user').value) return;
  runtimeOutput.replaceChildren();
  for (const component of status.components) runtimeOutput.append(text('p', `${component.component} · ${component.healthy ? '正常' : '离线'}${component.model_ready ? ' · 模型就绪' : ''}`));
  for (const [name, rows] of Object.entries(status.queues)) runtimeOutput.append(text('p', `${name}：${rows.map(row => `${row.status} ${row.count}`).join(' · ') || '暂无任务'}`));
  for (const alert of status.alerts) runtimeOutput.append(text('p', `告警：${alert.component} · ${alert.code}`));
  const values = status.api.latency_seconds;
  runtimeOutput.append(text('p', `API P50 ${values.p50?.toFixed(3) ?? '-'}s · P95 ${values.p95?.toFixed(3) ?? '-'}s · P99 ${values.p99?.toFixed(3) ?? '-'}s`));
  for (const failure of status.failures) runtimeOutput.append(text('p', `${failure.id.slice(0, 8)} · ${failure.error_code || failure.status} · trace ${failure.trace_id}`));
  for (const metric of status.metrics_last_hour) runtimeOutput.append(text('p', `${metric.component} ${metric.metric}：${metric.observations} 次 · 失败 ${metric.failures} · 平均 ${(metric.total_seconds / metric.observations).toFixed(3)}s · 最大 ${metric.max_seconds.toFixed(3)}s`));
}), runtimeOutput);
document.querySelector('main').append(runtimeSection);
const oldAccess = refreshGraphAccess;
refreshGraphAccess = async () => { await oldAccess(); runtimeSection.hidden = el('graph-maintenance').hidden; };
el('user').addEventListener('change', () => { runtimeSection.hidden = true; runtimeOutput.replaceChildren(); });
refreshGraphAccess().catch(fail);

const retryButton = command('重试任务', async () => {
  await api(`/api/v1/tasks/${current}/retry`, {method: 'POST', body: JSON.stringify({expected_version: version})});
  await detail(); await refresh(); if (subscription) subscription.abort(); watch(current);
});
retryButton.hidden = true; el('cancel').before(retryButton);
const previousDetail = detail;
detail = async () => {
  retryButton.hidden = true;
  const task = await previousDetail();
  if (!task) return task;
  retryButton.hidden = task.status !== 'failed' || task.manual_retries >= 3 || !['dependency_unavailable','task_timeout','task_deadline_exhausted','attempt_budget_exhausted','task_execution_failed','answer_unavailable','retrieval_unavailable','dialogue_node_timeout','dialogue_node_failed'].includes(task.error_code);
  if (task.status === 'not_enabled') el('cancel').disabled = true;
  return task;
};
