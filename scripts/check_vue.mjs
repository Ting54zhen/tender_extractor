// Vue 页面回归：Node 原生 HTTP + CDP，真实 Chrome 渲染，不安装测试框架。
// API 使用脱敏、可修改的内存夹具；验证页面发送的请求与用户可观察行为，
// 不访问模型、不读 data/ 采购文件，也不把前端回归冒充真实文档生成验收。
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn, execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FRONTEND = path.join(ROOT, 'frontend', 'dist');
const OUT = path.join(ROOT, 'data', 'vue-browser-check');
const CHROME = [process.env.TENDER_CHROME, 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'].find(file => file && fs.existsSync(file));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const results = [], requests = [], events = [];
let chrome, ws, server, profile, base, previewCalls = 0, lastGenerated = null, previewMode = '', lastSettings = null, lastCompany = null;
// 运行状态由夹具明确切换，验证百分比、刷新恢复和错误态，避免使用真实模型。
let extractionState = 'done';
const deletedJobs = new Set();
let manyRequirements = false, rejectDelete = false;
function extractionSnapshot(id = 'job-one') {
  return { ...extractJob, id, status: extractionState, progress: extractionState === 'running' ? 35 : extractionState === 'error' ? 65 : 100,
    stage: extractionState === 'running' ? '规则抽取完成，正在处理模型补缺' : extractionState === 'error' ? '失败' : '完成', error: extractionState === 'error' ? '模拟解析失败' : '' };
}
let templates = [{ id: 'builtin-service', name: '某服务类模板', builtin: true, is_default: true, category: 'service', method: 'open', placeholders: ['项目名称', '采购预算', '采购标的', '未知位置'], unmapped: ['未知位置'], placeholder_count: 4 }];
let projects = [{ id: 'project-one', name: '某采购项目', note: '脱敏回归', status: 'active', job_count: 1 }];
const result = { fields: { project_name: { key: 'project_name', label: '项目名称', template: '项目名称', value: '<img src=x onerror="window.__injected=1">', status: 'ok', confidence: 0.9, source: 'rule', evidence: [{ block_id: 'p1', quote: '<script>window.__injected=2</script>', section: '一、采购需求' }] } }, review: [], template_values: { '项目名称': '某项目' }, missing_templates: [] };
const extractJob = { id: 'job-one', kind: 'extract', filename: '脱敏报告.docx', status: 'done', stage: '完成', elapsed: 1.2, summary: { fields_with_value: 3, templates_filled: 3, templates_total: 4 }, downloads: { json: '/api/jobs/job-one/download/json', md: '/api/jobs/job-one/download/md', draft: '' } };
const compareJob = { id: 'compare-one', kind: 'compare', status: 'done', stage: '完成', elapsed: 1, summary: { requirements: 1, responses: 1, satisfied: 1 } };
const settings = { llm: { enabled: true, provider: 'ollama', base_url: 'http://127.0.0.1:11434', model: 'local-model' }, review: { enabled: false, provider: 'ollama' }, extraction: { min_confidence: 0.6, require_tender_document: true }, output: { dir: '' } };
function field(key, label, value, extra = {}) { return { key, label, template: label, value, status: value ? 'ok' : 'missing', confidence: value ? 0.95 : 0, evidence: [{ quote: '某项目原文', section: '一、采购需求' }], ...extra }; }
function fields(query) {
  return { groups: [{ key: 'general', label: '基本信息', items: [field('project_name', '项目名称', '某项目'), field('budget_amount', '采购预算', '123456789.1234')] }, { key: 'requirements', label: '采购要求', items: [field('procurement_target', '采购标的', ''), { key: 'procurement_target_list', label: '标的清单', value: '1. 某设备（数量1）\n2. 某服务（数量2）', template: '' }, field('contract_period', '履行期限', '')] }],
    template: { id: query.get('template_id') || templates[0].id, name: templates[0].name }, unmapped_placeholders: ['未知位置'],
    scope: { complete: ['method', 'category', 'org_form'].every(key => !!query.get(key)), axes: { method: [{ code: 'open', label: '公开招标' }], category: [{ code: 'service', label: '服务' }, { code: 'goods', label: '货物' }], org_form: [{ code: 'self', label: '自行组织' }] }, suggested: {}, na: {}, conflicts: [], extra_fields: [], template_hint: null } };
}
function preview(values = {}, custom = []) {
  const entries = Object.entries(values);
  return { stats: { filled: entries.filter(([, value]) => value).length, total_placeholders: entries.length, missing: entries.filter(([, value]) => !value).length }, sections: [{ title: '第一章', blocks: [{ type: 'paragraph', style: 'h1', segments: [{ kind: 'text', text: '第一章 采购需求' }] }, ...entries.map(([name, value]) => ({ type: 'paragraph', style: 'body', segments: [{ kind: value ? 'value' : 'missing', name, text: value || '【待补充：' + name + '】' }] })), { type: 'table', rows: [[{ colspan: 2, rowspan: 1, segments: [{ kind: 'text', text: '<img src=x onerror="window.__injected=3">' }] }, { skip: true }]] }, ...custom.map(item => ({ type: 'paragraph', style: 'body', segments: [{ kind: 'value', name: item.placeholder || item.label, text: item.value }] }))] }] };
}

// 夹具服务器保留生产路由和二进制上传方式，记录写请求供断言核对。
async function handle(req, res) {
  const url = new URL(req.url, base), chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks), text = body.toString('utf8');
  let data = {};
  if ((req.headers['content-type'] || '').includes('application/json')) data = JSON.parse(text || '{}');
  requests.push({ method: req.method, path: url.pathname, query: url.search, data, body: text });
  const json = (value, status = 200) => { res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' }); res.end(JSON.stringify(value)); };
  const binary = name => { res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': "attachment; filename*=UTF-8''" + encodeURIComponent(name) }); res.end('fixture download'); };
  if (url.pathname === '/api/health') return json({ available: true, model: 'local-model', provider: 'ollama' });
  if (url.pathname === '/api/settings') { if (req.method === 'POST') lastSettings = data; return json({ settings: data.settings || settings, overrides: data.settings || {} }); }
  if (url.pathname === '/api/company') { if (req.method === 'POST') lastCompany = data; return json({ profile: req.method === 'POST' ? data : { company: { name: '某单位' }, qualifications: [], performances: [], personnel: [], statements: { service: '保留原有承诺' }, evidence_index: { license: '本机材料' } } }); }
  if (url.pathname === '/api/projects') {
    if (req.method === 'POST') { projects.push({ id: 'project-new', ...data, status: 'active', job_count: 0 }); return json(projects.at(-1), 201); }
    return json({ projects: projects.filter(item => !url.searchParams.get('status') || item.status === url.searchParams.get('status')) });
  }
  if (url.pathname.startsWith('/api/projects/')) {
    const [, , , id, action] = url.pathname.split('/'), project = projects.find(item => item.id === id);
    if (action === 'export') return binary('项目导出.zip');
    if (action === 'rename') project.name = data.name;
    if (action === 'archive') project.status = 'archived';
    if (action === 'restore') project.status = 'active';
    if (action === 'delete') projects = projects.filter(item => item.id !== id);
    return json({ ...project, jobs: [extractJob] });
  }
  if (url.pathname === '/api/templates') {
    if (req.method === 'POST') { const value = { ...templates[0], id: 'user-template', builtin: false, is_default: false, name: url.searchParams.get('name'), category: url.searchParams.get('category'), method: url.searchParams.get('method') }; templates.push(value); return json(value, 201); }
    return json({ templates });
  }
  if (url.pathname.startsWith('/api/templates/')) {
    const [, , , id, action] = url.pathname.split('/'), item = templates.find(value => value.id === id);
    if (action === 'preview') return json(preview({ '项目名称': '', '采购预算': '' }));
    if (action === 'default') templates.forEach(value => { value.is_default = value.id === id; });
    if (action === 'rename') item.name = data.name;
    if (req.method === 'DELETE') { templates = templates.filter(value => value.id !== id); if (!templates.some(value => value.is_default)) templates[0].is_default = true; }
    return json(item || {});
  }
  if (url.pathname === '/api/jobs') return json({ jobs: (manyRequirements ? Array.from({ length: 16 }, (_, index) => extractionSnapshot('list-' + index)) : [extractionSnapshot()]).filter(item => !deletedJobs.has(item.id)) });
  if (url.pathname === '/api/extract') return json(extractionSnapshot(), 202);
  if (url.pathname === '/api/compare') return json(compareJob, 202);
  if (url.pathname.startsWith('/api/jobs/')) {
    const parts = url.pathname.split('/'), id = parts[3], action = parts[4];
    if (!action && req.method === 'DELETE') {
      if (rejectDelete) return json({ error: '模拟删除失败，请重试' }, 409);
      deletedJobs.add(id); return json({ deleted: id });
    }
    if (!action) return id === 'missing-job' || deletedJobs.has(id) ? json({ error: '任务不存在' }, 404) : json(id.startsWith('compare') ? compareJob : extractionSnapshot(id));
    if (action === 'fields') return json(fields(url.searchParams));
    if (action === 'result') return json(result);
    if (action === 'report') return json({ markdown: '# 某报告\n\n<script>window.__injected=4</script>\n\n证据需要人工复核。' });
    if (action === 'compare') return json({ results: [{ requirement: '某资格要求', req_evidence: '招标原文', verdict: 'satisfied', matched_response: '某响应', resp_evidence: '投标原文', note: '请人工确认' }], report: '某比对报告' });
    if (action === 'suggest') return json({ suggestions: { contract_period: { value: '人工确认后采用', reason: '测试建议', source: 'llm_suggest' } }, count: 1 });
    if (action === 'preview') {
      previewCalls += 1;
      const output = preview(data.values, data.custom_fields);
      if (previewMode === 'race') await sleep(data.values['项目名称'] === '旧响应' ? 1200 : 20);
      return json(output);
    }
    if (action === 'generate') { lastGenerated = data; return binary('某招标文件.docx'); }
    if (action === 'download') return binary('审核报告.json');
    if (action === 'export') return binary('某招标文件.pdf');
    if (action === 'bid') return json({ summary: { covered: 1, qualifications: 1, sections: 1, gaps: 0 }, gaps: [], markdown: '# 投标书初稿' });
    if (action === 'analyze') return json({ graph: { nodes: [{ id: 'n1', type: 'Project', label: '某项目' }], edges: [] }, knowledge_report: '# 风险报告', turtle: 'fixture turtle' });
  }
  const pages = { '/': 'index.html', '/fill': 'fill.html', '/templates': 'templates.html', '/projects': 'projects.html', '/company': 'company.html', '/settings': 'settings.html', '/compare': 'compare.html' };
  const relative = pages[url.pathname] || url.pathname.slice(1);
  const file = path.resolve(FRONTEND, relative);
  if (!file.startsWith(FRONTEND + path.sep) || !fs.existsSync(file)) return json({ error: 'not found' }, 404);
  res.writeHead(200, { 'Content-Type': ({ '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' })[path.extname(file)] || 'text/plain', 'Cache-Control': 'no-store' }); res.end(fs.readFileSync(file));
}

async function waitFor(fn, label, timeout = 10000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await fn(); if (value) return value; await sleep(50); }
  throw new Error('等待超时：' + label);
}
async function main() {
  assert.ok(CHROME, '需要已安装的 Chrome/Edge');
  fs.mkdirSync(OUT, { recursive: true });
  profile = fs.mkdtempSync(path.join(OUT, 'tender-vue-'));
  server = http.createServer((req, res) => handle(req, res).catch(error => { res.writeHead(500); res.end(error.stack); }));
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  base = 'http://127.0.0.1:' + server.address().port;
  chrome = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', '--user-data-dir=' + profile, '--window-size=1440,1000', 'about:blank'], { stdio: 'ignore', windowsHide: true });
  const activePortFile = path.join(profile, 'DevToolsActivePort');
  await waitFor(() => fs.existsSync(activePortFile), 'Chrome 调试端口', 30000);
  const port = fs.readFileSync(activePortFile, 'utf8').split('\n')[0];
  const tabs = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
  ws = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, { once: true }); ws.addEventListener('error', reject, { once: true }); });
  let sequence = 0; const pending = new Map();
  ws.addEventListener('message', event => {
    const value = JSON.parse(event.data);
    if (value.id) { const item = pending.get(value.id); if (item) { pending.delete(value.id); value.error ? item.reject(new Error(JSON.stringify(value.error))) : item.resolve(value.result); } }
    else events.push(value);
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  async function evaluate(expression) {
    const value = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (value.exceptionDetails) throw new Error(value.exceptionDetails.exception?.description || value.exceptionDetails.text);
    return value.result.value;
  }
  const run = async (name, fn) => { await fn(); results.push(name); console.log('[vue] OK ' + name); };
  const waitDom = expression => waitFor(() => evaluate(expression), expression);
  const click = selector => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const input = (selector, value, event = 'input') => evaluate(`(() => { const element = document.querySelector(${JSON.stringify(selector)}); element.value = ${JSON.stringify(value)}; element.dispatchEvent(new Event(${JSON.stringify(event)}, {bubbles:true})); })()`);
  async function go(route) { await send('Page.navigate', { url: base + route }); await waitDom(`document.querySelector('#vue-root')?.__vue_app__ && !document.querySelector('[v-cloak]')`); await sleep(80); }
  async function shot(name) { const image = await send('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(image.data, 'base64')); }
  for (const domain of ['Page', 'Runtime', 'Network', 'Log']) await send(domain + '.enable');
  await send('Browser.setDownloadBehavior', { behavior: 'allow', downloadPath: OUT });
  await run('七个多页面路径与 .html 别名均可挂载 Vue', async () => {
    for (const route of ['/', '/fill', '/templates', '/projects', '/company', '/settings', '/compare', '/index.html', '/fill.html', '/templates.html', '/projects.html', '/company.html', '/settings.html', '/compare.html']) {
      await go(route); assert.equal(await evaluate(`document.querySelectorAll('.sidebar .sb-nav a').length`), 7);
    }
  });
  await go('/fill?job=job-one');
  await run('未选齐类型时阻止填写与生成', async () => { await waitDom(`!document.getElementById('scopeGate').hidden`); assert.equal(await evaluate(`document.getElementById('btnGenerate').disabled`), true); });
  for (const [id, value] of [['scopeMethod', 'open'], ['scopeCategory', 'service'], ['scopeOrgForm', 'self']]) await input('#' + id, value, 'change');
  await waitDom(`document.querySelectorAll('.field[data-key]').length === 2`);
  await run('金额原样展示且字段跨分组编辑与清空均保留', async () => {
    assert.equal(await evaluate(`document.querySelector('[data-key="budget_amount"] [data-input]').value`), '123456789.1234');
    await input('[data-key="project_name"] [data-input]', '跨分组修改'); await click('[data-key="budget_amount"] [data-act="clear"]');
    await click('[data-g="requirements"]'); await waitDom(`document.querySelector('[data-key="procurement_target"]')`);
    await click('[data-key="procurement_target"] [data-act="import-list"]'); await click('#btnGenerate');
    await waitFor(() => lastGenerated, '生成请求'); assert.equal(lastGenerated.values['项目名称'], '跨分组修改'); assert.equal(lastGenerated.values['采购预算'], ''); assert.equal(lastGenerated.values['采购标的'], '某设备\n某服务');
  });
  await run('建议先展示待确认，采纳后才写入预览与成稿', async () => {
    await click('#btnSuggest'); await waitDom(`document.querySelector('[data-act="use-sugg"]')`);
    assert.equal(await evaluate(`document.querySelector('[data-key="contract_period"] [data-input]').value`), '');
    await click('[data-act="use-sugg"]'); assert.equal(await evaluate(`document.querySelector('[data-key="contract_period"] [data-input]').value`), '人工确认后采用');
  });
  await run('Vue 结构化预览保留合并表格且原文不会作为 HTML 执行', async () => {
    await click('#btnPreview'); await waitDom(`document.querySelector('#paper td[colspan="2"]')`);
    assert.equal(await evaluate(`document.querySelectorAll('#paper img').length`), 0); assert.equal(await evaluate(`window.__injected || 0`), 0);
    await shot('01-填写预览');
  });
  await run('预览点击可跨分组定位字段', async () => {
    await click('#paper [data-pname="项目名称"]'); await waitDom(`document.querySelector('[data-key="project_name"].flash')`);
  });
  await run('并发预览丢弃旧响应，防抖后只显示最新值', async () => {
    previewMode = 'race'; await input('[data-key="project_name"] [data-input]', '旧响应');
    const before = previewCalls; await waitFor(() => previewCalls > before, '旧预览请求');
    await input('[data-key="project_name"] [data-input]', '最新值'); await waitDom(`document.querySelector('#paper').innerText.includes('最新值')`); await sleep(700);
    assert.equal(await evaluate(`document.querySelector('#paper').innerText.includes('旧响应')`), false); previewMode = '';
  });
  await run('自定义字段新增、卡片编辑、刷新恢复与任务隔离', async () => {
    await click('#btnAddCustom'); await input('#cfLabel', '自定义回归字段'); await input('#cfValue', '初始值'); await click('#cfSave');
    await click('[data-g="custom"]'); await waitDom(`document.querySelector('.field.custom [data-input]')`);
    await input('.field.custom [data-input]', '卡片修改值'); await go('/fill?job=job-one'); await waitDom(`document.querySelector('[data-g="custom"]')`); await click('[data-g="custom"]');
    assert.equal(await evaluate(`document.querySelector('.field.custom [data-input]').value`), '卡片修改值');
    await go('/fill?job=job-other'); await waitDom(`document.querySelector('#scopeMethod option[value="open"]')`); assert.equal(await evaluate(`localStorage.getItem('tender.customFields.job-other')`), null);
  });
  await go('/templates'); await waitDom(`document.querySelector('.tpl-card')`);
  await run('模板上传、设默认、重命名、预览和删除回退', async () => {
    await evaluate(`(() => { const file = new File(['fixture'], '脱敏模板.docx'); const dt = new DataTransfer(); dt.items.add(file); const input = document.getElementById('fileInput'); input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles:true})); })()`);
    await waitDom(`document.querySelector('#dlgUpload[open]')`); await input('#upName', '某自有模板'); await click('#upSave'); await waitDom(`document.querySelectorAll('.tpl-card').length === 2`);
    await evaluate(`Array.from(document.querySelectorAll('.tpl-card button')).find(b => b.textContent === '设为默认').click()`); await waitFor(() => templates.some(item => item.id === 'user-template' && item.is_default), '默认模板');
    // 服务端收到写请求时，页面可能仍在重载列表；等保存控件恢复才继续下一操作。
    await waitDom(`!document.querySelector('#rnSave').disabled && document.querySelector('#list').innerText.includes('默认')`);
    await evaluate(`Array.from(document.querySelectorAll('.tpl-card button')).find(b => b.textContent === '重命名').click()`); await input('#rnName', '某新名称'); await click('#rnSave'); await waitDom(`document.querySelector('#list').innerText.includes('某新名称')`);
    await click('[data-act="preview"]'); await waitDom(`document.querySelector('#pvPaper .pv-table')`); await shot('02-模板库'); await click('#dlgPreview .dlg-foot button');
    await evaluate(`Array.from(document.querySelectorAll('.tpl-card button')).find(b => b.textContent === '删除').click()`); await click('#cfOk'); await waitDom(`document.querySelectorAll('.tpl-card').length === 1`); assert.equal(templates[0].is_default, true);
  });
  await go('/settings'); await waitDom(`document.querySelector('#f-llm-model')`);
  await run('设置表单按原契约保存并保留密钥边界', async () => { await input('#f-llm-model', 'modified-local-model'); await click('#save'); await waitFor(() => lastSettings, '设置保存'); assert.equal(lastSettings.settings.llm.model, 'modified-local-model'); assert.equal('api_key' in lastSettings.settings.llm, false); });
  await go('/company'); await waitDom(`document.querySelector('[data-basic="name"]')`);
  await run('企业档案保留列表与未编辑的承诺字段', async () => { await input('[data-basic="name"]', '某企业'); await click('#add-q'); await waitDom(`document.querySelector('#qualifications [data-f="name"]')`); await input('#qualifications [data-f="name"]', '某证书'); await click('#save'); await waitFor(() => lastCompany, '档案保存'); assert.equal(lastCompany.company.name, '某企业'); assert.equal(lastCompany.qualifications[0].name, '某证书'); assert.equal(lastCompany.statements.service, '保留原有承诺'); });
  await go('/projects'); await waitDom(`document.querySelector('.pcard')`);
  await run('项目新建、详情与普通任务链接', async () => { await click('#new-project'); await input('#np-name', '某新项目'); await click('#np-create'); await waitDom(`document.querySelectorAll('.pcard').length === 2`); await click('.pcard'); await waitDom(`document.querySelector('#detail-jobs a')`); assert.equal(await evaluate(`document.querySelector('#detail-jobs a').getAttribute('href')`), '/fill.html?job=job-one'); });
  await run('项目返回按钮黑底白字，删除弹窗居中且文案不变，取消不提交', async () => {
    assert.deepEqual(await evaluate(`(() => { const style = getComputedStyle(document.querySelector('#back')); return [style.backgroundColor, style.color]; })()`), ['rgb(17, 17, 16)', 'rgb(255, 255, 255)']);
    await click('#back'); await waitDom(`document.querySelector('#list-view').style.display !== 'none'`);
    const calls = requests.filter(item => item.path.endsWith('/delete')).length;
    await click('.pcard .danger'); await waitDom(`document.querySelector('.delete-dialog')?.open`);
    assert.equal(await evaluate(`document.querySelector('#delete-message').textContent`), '删除项目「某采购项目」？项目下的任务与字段确认值将一并删除，不可恢复。');
    assert.equal(await evaluate(`document.querySelector('[data-delete-confirm]').textContent`), '确认删除');
    assert.deepEqual(await evaluate(`(() => { const r = document.querySelector('.delete-dialog').getBoundingClientRect(); return [Math.abs(r.x + r.width / 2 - innerWidth / 2) < 1, Math.abs(r.y + r.height / 2 - innerHeight / 2) < 1]; })()`), [true, true]);
    await shot('09-项目删除确认'); await click('[data-delete-cancel]');
    assert.equal(requests.filter(item => item.path.endsWith('/delete')).length, calls);
    await click('.pcard .danger'); await click('[data-delete-confirm]');
    await waitDom(`document.querySelectorAll('.pcard').length === 1`);
    assert.equal(requests.filter(item => item.path.endsWith('/delete')).length, calls + 1);
  });
  await run('项目空状态在主区中央', async () => {
    await click('[data-status="archived"]'); await waitDom(`document.querySelector('#grid .empty')`);
    assert.deepEqual(await evaluate(`(() => { const grid = document.querySelector('#grid').getBoundingClientRect(), empty = document.querySelector('#grid .empty').getBoundingClientRect(); return [Math.abs(empty.x + empty.width / 2 - grid.x - grid.width / 2) < 1, Math.abs(empty.y + empty.height / 2 - grid.y - grid.height / 2) < 1]; })()`), [true, true]);
    await shot('10-项目空状态');
  });
  await go('/?job=job-one'); await waitDom(`document.querySelector('#pane-fields tbody tr')`);
  await run('解析结果与审核报告转义模型文本，并保留证据块标识', async () => { assert.equal(await evaluate(`document.querySelectorAll('#pane-fields img, #pane-report script').length`), 0); assert.ok(await evaluate(`document.querySelector('#pane-fields').textContent.includes('p1')`)); assert.equal(await evaluate(`window.__injected || 0`), 0); await sleep(350); await shot('03-解析结果'); });
  await run('保留原投标初稿与图谱展示入口', async () => { await click('#gen-bid'); await waitDom(`document.querySelector('#bid-result')`); await click('#gen-risk'); await waitDom(`document.querySelector('#graph-svg svg')`); });
  await go('/compare?job=compare-one'); await waitDom(`document.querySelector('#tbody tr')`);
  await run('比对历史任务恢复与证据展示', async () => { assert.ok(await evaluate(`document.querySelector('#tbody').innerText.includes('招标原文') && document.querySelector('#tbody').innerText.includes('投标原文')`)); });
  // 上传通过 File + DataTransfer 触发真实 change 事件，验证 multipart 与二进制契约，
  // 不直接调用组件方法，以便发现 Vue 模板漏绑事件或表单字段名被改掉的问题。
  async function chooseFile(selector, name, content = '脱敏测试内容') {
    await evaluate(`(() => {
      const input = document.querySelector(${JSON.stringify(selector)}), dt = new DataTransfer();
      dt.items.add(new File([${JSON.stringify(content)}], ${JSON.stringify(name)}));
      input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
    })()`);
  }
  await run('需求输入框随内容增高、上限250px、删减收回且禁止手动拉伸', async () => {
    await go('/?view=new');
    const height = () => evaluate(`document.querySelector('#requirement-text').getBoundingClientRect().height`);
    const initial = await height();
    await input('#requirement-text', Array(6).fill('某采购项目需求').join('\n'));
    const expanded = await height(); assert.ok(expanded > initial && expanded <= 250);
    await input('#requirement-text', Array(40).fill('采购需求详细内容').join('\n'));
    assert.equal(await height(), 250);
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('#requirement-text')).overflowY`), 'auto');
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('#requirement-text')).resize`), 'none');
    await input('#requirement-text', '短需求'); assert.equal(await height(), initial);
    await input('#requirement-text', '采购需求'.repeat(110));
    await send('Emulation.setDeviceMetricsOverride', { width: 800, height: 900, deviceScaleFactor: 1, mobile: false }); await sleep(150);
    assert.ok(await height() <= 250); assert.ok(await evaluate(`document.querySelector('#requirement-text').scrollWidth <= document.querySelector('#requirement-text').clientWidth + 1`));
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  });
  await run('文件拖入显示松开提示、跨子元素不闪烁、离开与放下清除', async () => {
    await go('/?view=new');
    // 构造浏览器文件拖动事件，分别验证输入框、子元素边界和 drop 的实际处理。
    async function drag(selector, type, files = true) {
      await evaluate(`(() => { const dt = new DataTransfer(); ${files ? "dt.items.add(new File(['某需求原文'], '拖入需求.txt'));" : "dt.setData('text/plain', '普通文字');"} document.querySelector(${JSON.stringify(selector)}).dispatchEvent(new DragEvent(${JSON.stringify(type)}, { bubbles: true, cancelable: true, dataTransfer: dt })); })()`);
    }
    const visible = () => evaluate(`getComputedStyle(document.querySelector('.drop-upload-hint')).display !== 'none'`);
    await drag('#requirement-text', 'dragenter', false); assert.equal(await visible(), false);
    await drag('#requirement-text', 'dragenter'); await drag('#requirement-text', 'dragover'); assert.equal(await visible(), true);
    assert.ok(await evaluate(`document.querySelector('.drop-upload-hint').textContent.includes('松开即可上传')`));
    await shot('07-松开上传提示');
    await drag('#drop .big', 'dragenter'); await drag('#requirement-text', 'dragleave'); assert.equal(await visible(), true);
    await drag('#drop .big', 'dragleave'); assert.equal(await visible(), false);
    await drag('#requirement-text', 'dragenter'); await drag('#requirement-text', 'drop'); assert.equal(await visible(), false);
    await waitDom(`document.querySelector('#file-name').textContent.includes('拖入需求.txt')`);
  });
  await run('上传和移除文件前后三档布局坐标一致，长文件名不撑开页面', async () => {
    // 比较用户可见元素的真实坐标与尺寸，直接复现上传导致重排和滚动条挤压的问题。
    for (const width of [1440, 1100, 800]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height: 700, deviceScaleFactor: 1, mobile: false });
      await go('/?view=new'); await input('#requirement-text', '保留的采购需求草稿');
      const rects = () => evaluate(`['.composer', '#requirement-text', '.composer-bar', '#run', '.sugg-tabs', '.hero-title'].map(selector => { const rect = document.querySelector(selector).getBoundingClientRect(); return [rect.x, rect.y, rect.width, rect.height]; })`);
      const before = await rects();
      await chooseFile('#file', '采购需求报告文件名很长'.repeat(8) + '.docx');
      assert.deepEqual(await rects(), before, '上传 ' + width);
      assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`));
      if (width === 1440) await shot('08-上传后保持对齐');
      await click('#file-clear'); assert.deepEqual(await rects(), before, '移除 ' + width);
    }
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  });
  await go('/');
  await run('需求标题、搜索计数和切换状态布局稳定', async () => {
    manyRequirements = true;
    for (const width of [1440, 1100, 800]) {
      await send('Emulation.setDeviceMetricsOverride', { width, height: 700, deviceScaleFactor: 1, mobile: false });
      await go('/'); await waitDom(`document.querySelectorAll('#requirements-grid .pcard').length === 16`);
      assert.equal(await evaluate(`document.title`), '需求解析');
      const positions = () => evaluate(`['.page-head', '#requirement-status', '.requirement-search', '.search-summary'].map(selector => { const r = document.querySelector(selector).getBoundingClientRect(); return [r.x, r.y, r.width, r.height]; })`);
      const before = await positions();
      for (const value of ['running', 'error', 'done', 'all']) { await click('[data-filter="' + value + '"]'); assert.deepEqual(await positions(), before, width + ' ' + value); }
      await input('.requirement-search', '不存在');
      await waitDom(`document.querySelector('.search-summary').textContent.includes('搜索结果 共0条')`);
      assert.deepEqual(await positions(), before);
      await input('.requirement-search', '脱敏'); await waitDom(`document.querySelector('.search-summary').textContent.includes('搜索结果 共16条')`);
      if (width === 1440) await shot('11-需求搜索计数');
    }
    manyRequirements = false;
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await go('/');
  });
  await run('需求首页为工作台卡片，支持搜索和打开详情', async () => {
    await waitDom(`document.querySelector('#requirements-grid .pcard')`);
    assert.equal(await evaluate(`document.querySelector('#requirement-text')`), null);
    await input('.requirement-search', '不存在'); await waitDom(`document.querySelectorAll('#requirements-grid .pcard').length === 0`);
    await input('.requirement-search', '脱敏'); await waitDom(`document.querySelector('#requirements-grid .pcard')`);
    await shot('04-需求工作台'); await click('#requirements-grid .pcard');
    await waitDom(`document.querySelector('#pane-fields tbody tr')`);
    assert.equal(await evaluate(`new URLSearchParams(location.search).get('job')`), 'job-one');
    assert.equal(await evaluate(`document.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')`), '100');
  });
  await run('需求详情返回和删除样式、取消不删、失败留窗及确认后返回列表', async () => {
    await go('/?job=delete-ui'); await waitDom(`document.querySelector('#pane-fields tbody tr')`);
    assert.equal(await evaluate(`document.title`), '需求详情');
    assert.deepEqual(await evaluate(`(() => { const s = getComputedStyle(document.querySelector('#requirement-back')); return [s.backgroundColor, s.color]; })()`), ['rgb(17, 17, 16)', 'rgb(255, 255, 255)']);
    assert.deepEqual(await evaluate(`(() => { const s = getComputedStyle(document.querySelector('#delete-requirement')); return [s.color, s.backgroundColor, s.borderTopColor]; })()`), ['rgb(178, 58, 44)', 'rgb(252, 241, 239)', 'rgb(178, 58, 44)']);
    const calls = () => requests.filter(item => item.method === 'DELETE' && item.path === '/api/jobs/delete-ui').length;
    await click('#delete-requirement'); await waitDom(`document.querySelector('.delete-dialog').open`); await click('[data-delete-cancel]'); assert.equal(calls(), 0);
    rejectDelete = true; await click('#delete-requirement'); await click('[data-delete-confirm]');
    await waitDom(`document.querySelector('.delete-dialog .alert')?.textContent.includes('模拟删除失败')`);
    assert.equal(await evaluate(`document.querySelector('.delete-dialog').open`), true);
    rejectDelete = false; await click('[data-delete-confirm]'); await waitDom(`location.search === '' && document.querySelector('#requirements-grid')`);
    assert.equal(calls(), 2); assert.ok(deletedJobs.has('delete-ui'));
    extractionState = 'running'; await go('/?job=job-one'); await waitDom(`document.querySelector('#delete-requirement')?.disabled`);
    extractionState = 'done';
  });
  await go('/?view=new');
  await run('新建解析与侧栏需求列表分离，浏览器标题对应当前页面', async () => {
    assert.equal(await evaluate(`document.title`), '新建解析');
    assert.equal(await evaluate(`document.querySelectorAll('.sb-nav .active').length`), 0);
    await go('/index.html?view=new'); assert.equal(await evaluate(`document.querySelectorAll('.sb-nav .active').length`), 0);
  });
  await run('上传校验、移除文件和纯规则任务轮询按原契约工作', async () => {
    await chooseFile('#file', '不支持.exe'); await waitDom(`document.querySelector('#error-card').innerText.includes('仅支持')`);
    await chooseFile('#file', '脱敏报告.docx'); await click('#file-clear');
    await waitDom(`document.querySelector('#run').disabled`);
    await chooseFile('#file', '脱敏报告.docx');
    await click('input[name="mode"][value="rules"]'); await click('#run');
    await waitDom(`document.querySelector('#pane-fields tbody tr')`);
    const request = requests.findLast(item => item.path === '/api/extract');
    assert.equal(new URLSearchParams(request.query).get('use_llm'), '0');
    assert.equal(request.body, '脱敏测试内容');
    assert.equal(await evaluate(`document.querySelector('[role="progressbar"]').getAttribute('aria-valuenow')`), '100');
    assert.equal(await evaluate(`document.querySelector('#requirement-text')`), null);
  });
  await run('文字解析跳转详情、真实阶段百分比和刷新恢复', async () => {
    extractionState = 'running';
    await go('/?view=new'); await waitDom(`document.querySelector('#requirement-text')`);
    assert.equal(await evaluate(`document.querySelector('#run').disabled`), true);
    await input('#requirement-text', '   '); assert.equal(await evaluate(`document.querySelector('#run').disabled`), true);
    const content = '采购需求报告\n项目名称：某采购项目\n采购预算：100000元';
    await input('#requirement-text', content); await shot('05-新建文字解析'); await click('#run');
    await waitDom(`location.search.includes('job=job-one') && document.querySelector('#status-percent')?.textContent === '35%'`);
    const request = requests.findLast(item => item.path === '/api/extract');
    assert.equal(new URLSearchParams(request.query).get('filename'), '文字采购需求.txt'); assert.equal(request.body, content);
    assert.equal(await evaluate(`getComputedStyle(document.querySelector('.bar.determinate i')).animationName`), 'none');
    await shot('06-解析百分比'); await go('/?job=job-one');
    await waitDom(`document.querySelector('#status-percent')?.textContent === '35%'`);
    extractionState = 'done'; await waitDom(`document.querySelector('#pane-fields tbody tr')`);
    assert.equal(await evaluate(`document.querySelector('#status-percent').textContent`), '100%');
  });
  await run('拖放报告、提示词保留和文件优先规则', async () => {
    await go('/?view=new'); await input('#requirement-text', '保留的文字草稿');
    assert.ok(await evaluate(`document.querySelectorAll('.sugg-item').length > 0`));
    await evaluate(`(() => { const dt = new DataTransfer(); dt.items.add(new File(['拖放报告内容'], '拖放报告.txt')); document.querySelector('.composer').dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: dt })); })()`);
    await waitDom(`document.querySelector('#file-name').textContent.includes('拖放报告.txt')`);
    assert.equal(await evaluate(`document.querySelector('#requirement-text').disabled`), true);
    await click('#file-clear'); assert.equal(await evaluate(`document.querySelector('#requirement-text').value`), '保留的文字草稿');
    await chooseFile('#file', '报告优先.txt', '选中文件原文'); await click('#run');
    await waitDom(`document.querySelector('#pane-fields tbody tr')`);
    assert.equal(requests.findLast(item => item.path === '/api/extract').body, '选中文件原文');
  });
  await run('失败与不存在的详情保留错误且不伪报完成', async () => {
    extractionState = 'error'; await go('/?job=job-one');
    await waitDom(`document.querySelector('#error-text')?.textContent.includes('模拟解析失败')`);
    assert.equal(await evaluate(`document.querySelector('#status-percent').textContent`), '65%');
    assert.equal(await evaluate(`document.querySelector('#result')`), null);
    await go('/?job=missing-job'); await waitDom(`document.querySelector('#error-text')?.textContent.includes('任务不存在')`);
    extractionState = 'done';
  });
  await run('需求列表、新建与结果页三档布局无横向溢出', async () => {
    for (const route of ['/', '/?view=new', '/?job=job-one']) {
      await go(route);
      for (const width of [1440, 1100, 800]) { await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false }); await sleep(100); assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), route + ' ' + width); }
    }
  });
  await go('/compare');
  await run('比对提交保留 multipart 文件与 use_llm 字段', async () => {
    await chooseFile('#file-tender', '某招标文件.docx'); await chooseFile('#file-bid', '某投标文件.docx');
    await click('#use-llm'); await click('#run'); await waitDom(`document.querySelector('#tbody tr')`);
    const request = requests.findLast(item => item.path === '/api/compare');
    assert.ok(request.body.includes('name="tender"; filename="某招标文件.docx"'));
    assert.ok(request.body.includes('name="bid"; filename="某投标文件.docx"'));
    assert.match(request.body, /name="use_llm"\r\n\r\n0/);
  });
  await run('1440/1100/800 三档布局无横向溢出', async () => {
    await go('/fill?job=job-one'); await waitDom(`document.querySelector('.field')`);
    for (const width of [1440, 1100, 800]) { await send('Emulation.setDeviceMetricsOverride', { width, height: 1000, deviceScaleFactor: 1, mobile: false }); await sleep(100); assert.ok(await evaluate(`document.documentElement.scrollWidth <= innerWidth + 1`), String(width)); }
  });
  await run('没有外部资源请求、接口异常或浏览器未捕获错误', async () => {
    const external = events.filter(event => event.method === 'Network.requestWillBeSent').map(event => event.params.request.url).filter(url => !url.startsWith(base) && !/^(data|blob|about|chrome|devtools):/.test(url));
    const errors = events.filter(event => event.method === 'Runtime.exceptionThrown' || event.method === 'Runtime.consoleAPICalled' && event.params.type === 'error');
    assert.deepEqual(external, []); assert.deepEqual(errors.map(event => event.params), []);
    assert.equal(requests.some(request => request.path.includes('undefined')), false);
  });
}
try { await main(); }
catch (error) { console.error('[vue] FAIL', error.stack || error); console.error('[vue] 浏览器错误', JSON.stringify(events.filter(event => event.method === 'Runtime.exceptionThrown' || event.method === 'Runtime.consoleAPICalled' && event.params.type === 'error').map(event => event.params))); process.exitCode = 1; }
finally {
  try { ws?.close(); } catch { /* 已断开。 */ }
  if (chrome?.pid) {
    try { execFileSync('taskkill', ['/F', '/T', '/PID', String(chrome.pid)], { stdio: 'ignore', windowsHide: true }); } catch { /* Chrome 已退出。 */ }
  }
  server?.closeAllConnections(); server?.close();
  // 等待 Chrome 进程退出释放文件句柄；只删除本次创建且位于输出目录内的目录。
  if (chrome?.exitCode === null) await Promise.race([new Promise(resolve => chrome.once('exit', resolve)), sleep(3000)]);
  if (profile && path.dirname(profile) === path.resolve(OUT) && path.basename(profile).startsWith('tender-vue-')) {
    try { fs.rmSync(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }); } catch (error) { console.error('[vue] 临时浏览器目录待清理：' + profile, error.message); }
  }
  console.log('[vue] 完成 ' + results.length + ' 项；截图：' + OUT);
}
