// 浏览器实机回归：用真实 Chrome/Edge 跑前端交互，不安装任何 npm 包。
//
// 为什么用原生 CDP：playwright / agent-browser 都要额外装 CLI，而本仓库约束
// 不新增运行时依赖；Node 22+ 自带 WebSocket，配合 Chrome 的 --remote-debugging-port
// 就能驱动真实页面、收集 Network 与控制台事件、截图，零安装、零外链。
//
// 为什么把 exe 拷到空目录跑：同一份产物要既验证 frozen 资源解析，又验证真实
// 页面交互；空目录能排除开发源码「顺手补齐」的假阳性。
//
// 用法：node scripts/check_browser.mjs
//   TENDER_SAMPLE    指定一份 .docx 需求报告（否则在 data/ 与 samples/ 里找）
//   TENDER_CHROME    指定浏览器 exe（默认依次找 Chrome / Edge）
//   TENDER_BROWSER_PORT / TENDER_CDP_PORT  端口（默认 8899 / 9333）
// 默认验证打包产物；TENDER_CHECK_URL 可指向隔离的源码服务，复用同一交互检查。
// 源码模式不启动/终止外部服务，结束时只清理自己创建的浏览器目录。
import { spawn, execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXE_SRC = path.join(ROOT, 'dist', '招标需求解析.exe');
const CHECK_URL = process.env.TENDER_CHECK_URL || '';
const PORT = Number(process.env.TENDER_BROWSER_PORT || 8899);
const CDP_PORT = Number(process.env.TENDER_CDP_PORT || 9333);
const BASE = CHECK_URL || `http://127.0.0.1:${PORT}`;
const OUT = path.join(ROOT, 'data', 'browser-check');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CHROME = [
  process.env.TENDER_CHROME || '',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  '/usr/bin/google-chrome',
].find((p) => p && fs.existsSync(p)) || '';

const results = [];
const add = (name, pass, detail = '') => results.push({ name, pass: !!pass, detail: String(detail) });

async function jsonOrNull(res) {
  try { return await res.json(); } catch { return null; }
}

async function waitFor(fn, timeoutMs, label) {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    try { const v = await fn(); if (v) return v; } catch { /* 服务还没起来 */ }
    await sleep(500);
  }
  throw new Error(`等待超时：${label}`);
}

function killTree(proc) {
  if (!proc || !proc.pid) return;
  if (process.platform === 'win32') {
    // onefile 会拉起子进程，只杀引导器会留下占端口的孤儿进程
    try { execFileSync('taskkill', ['/F', '/T', '/PID', String(proc.pid)], { stdio: 'ignore', windowsHide: true }); } catch { /* 已退出 */ }
  } else {
    try { proc.kill('SIGKILL'); } catch { /* 已退出 */ }
  }
}

function findSample() {
  const override = process.env.TENDER_SAMPLE || '';
  if (override && fs.existsSync(override) && fs.statSync(override).isFile()) return override;
  const dirs = [
    path.join(ROOT, 'data', '归档', '结果审核报告', 'uploads'),
    path.join(ROOT, 'samples'),
    path.join(ROOT, 'data'),
  ];
  for (const dir of dirs) {
    if (!fs.existsSync(dir)) continue;
    const hit = fs.readdirSync(dir)
      .filter((n) => /\.docx$/i.test(n))
      .map((n) => path.join(dir, n))
      .find((p) => fs.statSync(p).isFile() && fs.statSync(p).size > 5000);
    if (hit) return hit;
  }
  return null;
}

function main() {
  if (!CHECK_URL && !fs.existsSync(EXE_SRC)) throw new Error('缺少 dist exe，请先跑 scripts/build_exe.py');
  if (!CHROME) throw new Error('找不到 Chrome/Edge，请设置 TENDER_CHROME 指向浏览器 exe');
  const sample = findSample();
  if (!sample) throw new Error('找不到 .docx 需求报告样本，请设置 TENDER_SAMPLE');

  fs.mkdirSync(OUT, { recursive: true });
  const tmp = fs.mkdtempSync(path.join(OUT, 'tender-cdp-'));
  const exe = path.join(tmp, '招标需求解析.exe');
  if (!CHECK_URL) fs.copyFileSync(EXE_SRC, exe);
  const profile = path.join(tmp, 'chrome-profile');

  let web = null;
  let chrome = null;
  let ws = null;
  const events = [];
  const pending = new Map();
  let seq = 0;

  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });

  const ev = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true });
    if (r.exceptionDetails) {
      return { __error: r.exceptionDetails.exception?.description || r.exceptionDetails.text };
    }
    return r.result.value;
  };

  const shot = async (name) => {
    const r = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, 'base64'));
  };

  const run = async () => {
    if (!CHECK_URL) web = spawn(exe, ['--no-browser', `--port=${PORT}`], { cwd: tmp, stdio: 'ignore', windowsHide: true });
    await waitFor(async () => {
      const r = await fetch(`${BASE}/api/health`);
      return r.ok ? true : null;
    }, 60000, 'exe 服务就绪');
    add(CHECK_URL ? '真实 Python 服务联通' : 'exe 在空目录独立启动', true, `${BASE}/api/health 200`);

    const buf = fs.readFileSync(sample);
    const created = await fetch(
      `${BASE}/api/extract?filename=${encodeURIComponent('browser-check.docx')}&use_llm=0&allow_non_tender=1`,
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: buf },
    );
    const job = await jsonOrNull(created);
    add('创建真实任务（纯规则，不调模型）', created.status === 202 && !!job?.id, `HTTP ${created.status}`);
    if (!job?.id) throw new Error('任务创建失败');
    const done = await waitFor(async () => {
      const r = await fetch(`${BASE}/api/jobs/${job.id}`);
      const j = await jsonOrNull(r);
      return j?.status === 'done' ? j : null;
    }, 60000, '抽取完成');
    add('抽取任务完成', true, `status=${done.status}`);

    chrome = spawn(CHROME, [
      '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
      '--remote-allow-origins=*', `--remote-debugging-port=${CDP_PORT}`,
      `--user-data-dir=${profile}`, '--window-size=1440,900', 'about:blank',
    ], { stdio: 'ignore', windowsHide: true });
    const target = await waitFor(async () => {
      const r = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const list = await r.json();
      return list.find((t) => t.type === 'page') || null;
    }, 30000, 'Chrome CDP');
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve, { once: true });
      ws.addEventListener('error', reject, { once: true });
    });
    ws.addEventListener('message', (e) => {
      const m = JSON.parse(e.data);
      if (m.id && pending.has(m.id)) {
        const p = pending.get(m.id);
        pending.delete(m.id);
        m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result);
      } else if (m.method) {
        events.push(m);
      }
    });
    for (const domain of ['Network', 'Runtime', 'Log', 'Page']) {
      await send(`${domain}.enable`);
    }
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    add('真实 Chrome headless 连接 CDP', true, target.webSocketDebuggerUrl.slice(0, 60));

    const go = async (url, waitMs = 2000) => {
      await send('Page.navigate', { url: BASE + url });
      await sleep(waitMs);
    };
    // 冷启动或刚跑完别的验收时固定 sleep 会偏短，关键步骤一律等 DOM 就绪；
    // 超时返回 null 而不是抛错，让后续断言照常给出失败原因
    const waitDom = async (expression, label, timeoutMs = 20000) => {
      try {
        return await waitFor(async () => (await ev(expression)) || null, timeoutMs, label);
      } catch { return null; }
    };

    await go('/');
    const home = await ev('({len: document.body.innerText.length, nav: document.querySelectorAll("header a, nav a").length})');
    add('首页在真实浏览器渲染', home?.len > 100 && home?.nav >= 5, JSON.stringify(home));

    await go('/templates', 1200);
    await waitDom(`document.querySelectorAll('.tpl-card').length >= 3`, '模板列表渲染');
    const cards = await ev(`(function(){
      var els=[].slice.call(document.querySelectorAll('.tpl-card'));
      return {count: els.length, text: els.map(function(e){return e.innerText;}).join(' | ')};
    })()`);
    add('模板库列表渲染三套内置模板',
      cards?.count >= 3 && /货物类/.test(cards.text) && /竞争性磋商/.test(cards.text),
      `${cards?.count} 张卡片`);
    await shot('01-模板库列表.png');
    await ev(`(function(){
      var b=document.querySelector('[data-act="detail"]'); if(!b) return null;
      b.click();
    })()`);
    // Vue 在事件结束后更新 DOM；等到实际占位符出现，避免只检查空弹窗。
    const detail = await waitDom(`(function(){
      var d=document.querySelector('#dlgDetail[open]');
      return d && d.querySelectorAll('.ph-list .row').length ? {open:true, text:d.innerText.slice(0,160)} : null;
    })()`, '模板占位符详情渲染');
    add('模板占位符详情弹窗可打开', !!detail?.open, JSON.stringify(detail));

    await go(`/fill?job=${job.id}`, 1200);
    await waitDom(
      `document.querySelectorAll('#scopeMethod option').length > 1 &&
       document.getElementById('loading').classList.contains('hidden')`,
      '填写页字段元数据就绪',
    );
    const gate = await ev(`({hidden: document.getElementById('scopeGate').hidden,
      appHidden: document.getElementById('app').classList.contains('hidden'),
      tpl: (document.getElementById('tplName')||{}).textContent || ''})`);
    add('v0.3 项目类型 gate 按设计生效',
      gate?.hidden === false && gate?.appHidden === true, JSON.stringify(gate));

    // v0.3 要求先选齐采购方式 / 业务类型 / 招标组织形式，字段区才加载
    const scoped = await ev(`(function(){
      var picked=[];
      ['scopeMethod','scopeCategory','scopeOrgForm'].forEach(function(id){
        var sel=document.getElementById(id);
        var opt=[].slice.call(sel.options).find(function(o){return o.value;});
        if(!opt) return;
        sel.value=opt.value; picked.push(id+'='+opt.value);
        sel.dispatchEvent(new Event('change',{bubbles:true}));
      });
      return picked;
    })()`);
    await waitDom(
      `document.querySelectorAll('.field[data-key]').length > 0`, '按类型加载字段'
    );
    const fill = await ev(`({fields: document.querySelectorAll('.field[data-key]').length,
      groups: document.querySelectorAll('#groups .field').length,
      gateHidden: document.getElementById('scopeGate').hidden,
      notice: (document.getElementById('notice')||{}).textContent || ''})`);
    add('选齐三项类型后按口径加载字段',
      fill?.fields > 0 && fill?.gateHidden === true,
      `选中 ${JSON.stringify(scoped)}；${JSON.stringify(fill)}`);

    const opened = await ev(`(function(){
      var b=document.getElementById('btnPreview'); if(!b) return false;
      b.click(); return true;
    })()`);
    await waitDom(`document.querySelectorAll('#paper .pv-table').length > 0`, '预览渲染');
    const paper = await ev(`(function(){
      var p=document.getElementById('paper');
      return {tables: p.querySelectorAll('.pv-table').length,
        segs: p.querySelectorAll('[data-pname]').length,
        spans: p.querySelectorAll('[colspan],[rowspan]').length,
        hasUndefined: /undefined/.test(p.innerHTML),
        len: p.innerText.length};
    })()`);
    add('生成前预览渲染章节/表格/占位符片段',
      opened && paper?.tables > 0 && paper?.segs > 0, JSON.stringify(paper));
    add('合并单元格渲染且不残留 skip/undefined',
      paper?.spans > 0 && !paper?.hasUndefined, JSON.stringify(paper));
    await shot('02-生成前预览.png');

    const edited = await ev(`(function(){
      var el=[].slice.call(document.querySelectorAll('.field[data-template]:not([data-na]) [data-input]'))
        .find(function(x){return (x.closest('.field').getAttribute('data-template')||'').trim();});
      if(!el) return null;
      var tpl=el.closest('.field').getAttribute('data-template').trim();
      el.value='CDP预览同步标记';
      el.dispatchEvent(new Event('input',{bubbles:true}));
      return tpl;
    })()`);
    const syncStart = Date.now();
    const synced = await waitDom(
      `document.getElementById('paper').innerText.indexOf('CDP预览同步标记')>=0`,
      '改字段后预览同步', 10000
    );
    add('改字段后预览同步（800ms debounce；不等同总耗时目标）', !!synced,
      `实测 ${Date.now() - syncStart}ms，占位符 ${edited}`);

    // 只点确实有对应字段的片段，避免点到「未绑定占位符」时按设计只弹 toast
    const located = await ev(`(function(){
      var f=[].slice.call(document.querySelectorAll('.field[data-template]:not([data-na])'))
        .find(function(x){return (x.getAttribute('data-template')||'').trim();});
      if(!f) return {clicked:false, reason:'no-field'};
      var tpl=f.getAttribute('data-template').trim();
      var seg=[].slice.call(document.querySelectorAll('#paper [data-pname]'))
        .find(function(s){return s.getAttribute('data-pname')===tpl;});
      if(!seg) return {clicked:false, reason:'no-seg', tpl:tpl};
      seg.click(); return {clicked:true, tpl:tpl};
    })()`);
    await sleep(1000);
    const flash = await ev(`document.querySelectorAll('.field.flash').length`);
    add('点击预览片段定位并高亮字段', located?.clicked && flash > 0,
      `${JSON.stringify(located)} flash=${flash}`);

    for (const width of [1440, 1100, 800]) {
      await send('Emulation.setDeviceMetricsOverride', {
        width, height: 900, deviceScaleFactor: 1, mobile: false,
      });
      await sleep(500);
      const m = await ev('({sw: document.documentElement.scrollWidth, iw: window.innerWidth})');
      add(`宽度 ${width}px 无横向溢出`, m && m.sw <= m.iw + 1, JSON.stringify(m));
    }
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1440, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await sleep(300);

    const dlg = await ev(`(function(){
      document.getElementById('btnAddCustom').click();
      return !!document.querySelector('dialog[open]');
    })()`);
    add('自定义字段弹窗可打开', !!dlg);
    await shot('03-自定义字段弹窗.png');
    // 从用户可见结果和持久化侧验证，不读取 Vue 内部组件状态。
    const saved = await ev(`(function(){
      document.getElementById('cfLabel').value='浏览器回归字段';
      document.getElementById('cfValue').value='BR-TEST-001';
      // 模拟真实输入事件，让 v-model 接收新值；直接赋 .value 不会触发输入。
      document.getElementById('cfLabel').dispatchEvent(new Event('input',{bubbles:true}));
      document.getElementById('cfValue').dispatchEvent(new Event('input',{bubbles:true}));
      document.getElementById('cfSave').click();
      var jid=new URLSearchParams(location.search).get('job');
      return {stored: localStorage.getItem('tender.customFields.'+jid),
        dlgOpen: !!document.querySelector('dialog[open]')};
    })()`);
    add('自定义字段新增并本地暂存',
      /浏览器回归字段/.test(saved?.stored || '') && /BR-TEST-001/.test(saved?.stored || ''),
      JSON.stringify(saved));

    await send('Page.reload');
    await waitDom(`document.querySelectorAll('.field[data-key]').length > 0`, '刷新后字段加载');
    await sleep(300);
    const persisted = await ev(`(function(){
      var jid=new URLSearchParams(location.search).get('job');
      var list=[]; try{list=JSON.parse(localStorage.getItem('tender.customFields.'+jid)||'[]');}catch(e){}
      return {count:list.length,
        hit:list.some(function(x){return x.label==='浏览器回归字段';}),
        fields: document.querySelectorAll('.field[data-key]').length,
        other: localStorage.getItem('tender.customFields.other-job-xyz')};
    })()`);
    add('刷新后自定义字段持久化', persisted?.hit === true, JSON.stringify(persisted));
    add('不同任务之间不串数据', persisted?.other === null, `other=${persisted?.other}`);

    // 读秒基准：切页再回来应从服务端 elapsed 继续，而不是从 0 重来。
    // 用 CDP 注入 fetch 桩模拟一个已运行 42 秒的进行中任务，避免依赖真实任务耗时。
    const mockSource = `(function(){
      var orig = window.fetch;
      window.fetch = function(url) {
        if (typeof url === 'string' &&
            (url === '/api/jobs' || url === '/api/jobs/mock-running')) {
          var running = { id: 'mock-running', kind: 'extract', status: 'running',
                          stage: '解析文档并抽取字段', use_llm: false, elapsed: 42.0 };
          var body = url === '/api/jobs' ? { jobs: [running] } : running;
          return Promise.resolve({ ok: true, status: 200,
            json: function(){ return Promise.resolve(body); } });
        }
        return orig.apply(this, arguments);
      };
    })();`;
    const injected = await send('Page.addScriptToEvaluateOnNewDocument', { source: mockSource });
    await go('/', 1500);
    await waitDom(`document.getElementById('status')?.classList.contains('show')`, '恢复状态卡片显示');
    const first = await ev(`document.getElementById('status-elapsed')?.textContent`);
    await go('/templates', 1000);
    await go('/', 1500);
    await waitDom(`document.getElementById('status')?.classList.contains('show')`, '再次返回状态卡片显示');
    const back = await ev(`document.getElementById('status-elapsed')?.textContent`);
    const firstSecs = parseFloat(first || '0');
    const backSecs = parseFloat(back || '0');
    add('切页返回后读秒从服务端耗时继续（不归零）',
        firstSecs >= 42 && firstSecs < 45 && backSecs >= 42 && backSecs < 45,
        `first=${first} back=${back}`);
    await send('Page.removeScriptToEvaluateOnNewDocument', { identifier: injected.identifier });

    // 填写工作台：切到别的页面再回来，已填的值不能归零
    await go(`/fill?job=${job.id}`, 1400);
    await waitDom(`document.querySelectorAll('.field[data-key]:not([data-na]) [data-input]').length > 0`,
      '填写页可编辑字段就绪');
    const draft = await ev(`(function(){
      var el=[].slice.call(document.querySelectorAll('.field[data-key]:not([data-na]) [data-input]'))[0];
      if(!el) return null;
      var f=el.closest('.field');
      el.value='CDP切页保留标记';
      el.dispatchEvent(new Event('input',{bubbles:true}));
      return {key: f.getAttribute('data-key') || '', tpl: f.getAttribute('data-template') || ''};
    })()`);
    await go('/templates', 800);
    await go(`/fill?job=${job.id}`, 1400);
    await waitDom(`document.querySelectorAll('.field[data-key]:not([data-na]) [data-input]').length > 0`,
      '返回填写页字段就绪');
    const backValue = await ev(`(function(){
      var key=${JSON.stringify(draft?.key || '')};
      var f=document.querySelector('.field[data-key="'+CSS.escape(key)+'"]');
      var el=f && f.querySelector('[data-input]');
      return el ? el.value : null;
    })()`);
    add('切页返回后填写值不归零', !!draft && backValue === 'CDP切页保留标记',
        `key=${draft?.key} before=CDP切页保留标记 after=${backValue}`);

    // L2 预览同屏：整页 A4 按栏宽缩放后不应再被横向裁切
    await ev(`(function(){ var p=document.getElementById('previewPane');
      if(p && p.hidden) document.getElementById('btnPreview').click(); return true; })()`);
    await waitDom(`document.querySelectorAll('#paper .pv-table').length > 0`, '缩放后预览纸面渲染');
    const fit = await ev(`(function(){
      var scroll=document.getElementById('pvScroll'), paper=document.getElementById('paper'),
          box=document.getElementById('paperFit');
      if(!scroll||!paper||!box) return null;
      var pr=paper.getBoundingClientRect(), sr=scroll.getBoundingClientRect();
      return {paperW: Math.round(pr.width), scrollW: scroll.clientWidth,
              fitW: Math.round(box.getBoundingClientRect().width),
              overflow: Math.round(pr.right - sr.right),
              transform: getComputedStyle(paper).transform};
    })()`);
    add('生成前预览整页在栏内完整可见',
        fit && fit.paperW <= fit.scrollW + 2 && fit.overflow <= 2, JSON.stringify(fit));

    // 预览面板开合状态按任务记住
    await go('/templates', 800);
    await go(`/fill?job=${job.id}`, 1400);
    await waitDom(`!document.getElementById('previewPane').hidden`, '返回填写页预览恢复');
    const pvState = await ev(`(function(){
      var p=document.getElementById('previewPane');
      return {open: p ? !p.hidden : null,
              stored: localStorage.getItem('tender.previewOpen.${job.id}')};
    })()`);
    add('切页返回后预览面板保持打开', pvState?.open === true, JSON.stringify(pvState));

    // AI 起草建议：样本里没有抽取值为空的字段，建议条按设计不会渲染，
    // 因此这里验证跨页暂存本身（写入 → 切页 → 内容仍在且未被清掉）。
    const suggKey = `tender.suggestions.${job.id}`;
    await ev(`localStorage.setItem(${JSON.stringify(suggKey)}, JSON.stringify({
      project_name: {value:'CDP建议保留标记', reason:'浏览器回归', source:'llm_suggest'}
    }))`);
    await go('/templates', 700);
    await go(`/fill?job=${job.id}`, 1400);
    await waitDom(`document.getElementById('loading').classList.contains('hidden')`, '建议暂存页就绪');
    const suggKept = await ev(`(function(){
      try {
        var raw = JSON.parse(localStorage.getItem(${JSON.stringify(suggKey)}) || '{}');
        return !!(raw.project_name && raw.project_name.value === 'CDP建议保留标记');
      } catch(e) { return false; }
    })()`);
    add('切页返回后 AI 起草建议暂存保留', suggKept === true,
        `key=${suggKey} kept=${suggKept}`);

    // 放到最后统计，覆盖上面所有导航（含注入 fetch 桩与切页往返）
    const external = [...new Set(events
      .filter((e) => e.method === 'Network.requestWillBeSent')
      .map((e) => e.params.request.url)
      .filter((u) => !u.startsWith(BASE) && !/^(data|about|blob|chrome|devtools):/.test(u)))];
    const errors = [];
    for (const e of events) {
      if (e.method === 'Runtime.exceptionThrown') {
        errors.push('uncaught: ' + (e.params.exceptionDetails.exception?.description || e.params.exceptionDetails.text));
      } else if (e.method === 'Log.entryAdded' && e.params.entry.level === 'error') {
        errors.push('log: ' + e.params.entry.text);
      } else if (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error') {
        errors.push('console: ' + e.params.args.map((a) => a.value ?? a.description ?? '').join(' '));
      }
    }
    const realErrors = errors.filter((x) => !/favicon\.ico/.test(x));
    add('页面无任何外部域名请求', external.length === 0, external.join(' | '));
    add('控制台无报错/未捕获异常', realErrors.length === 0, realErrors.slice(0, 3).join(' | '));
  };

  return run()
    .catch((err) => { add('执行异常', false, err.stack || String(err)); })
    .finally(async () => {
      try { ws?.close(); } catch { /* noop */ }
      killTree(chrome);
      killTree(web);
      // Windows 的浏览器子进程可能稍后才释放配置目录，先等退出再清理。
      if (chrome?.exitCode === null) await Promise.race([
        new Promise(resolve => chrome.once('exit', resolve)), sleep(3000),
      ]);
      // 只允许清理本次创建、位于回归输出目录内的工作目录。
      if (path.dirname(tmp) === path.resolve(OUT) && path.basename(tmp).startsWith('tender-cdp-')) {
        try { fs.rmSync(tmp, { recursive: true, force: true, maxRetries: 10, retryDelay: 300 }); }
        catch (error) { console.error(`[browser] 临时目录清理失败：${tmp} ${error.message}`); process.exitCode = 1; }
      }
      const failed = results.filter((r) => !r.pass);
      for (const r of results) {
        console.log(`[browser] ${r.pass ? 'OK  ' : 'FAIL'} ${r.name}${r.detail ? ' :: ' + r.detail.slice(0, 180) : ''}`);
      }
      console.log(`[browser] 检查项：${results.length - failed.length}/${results.length} 通过`);
      console.log(`[browser] 截图目录：${OUT}`);
      if (failed.length) process.exitCode = 1;
    });
}

main();
