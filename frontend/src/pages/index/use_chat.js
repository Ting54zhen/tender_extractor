import { onBeforeUnmount, ref } from 'vue';
import { api } from '../../services/api.js';
import { download } from '../../services/download.js';
import {
  ASK_TARGET_TEXT, HELP_TEXT, INPUT_LIMIT_TEXT, MAX_CHAT_TEXT_CHARS,
  OUT_OF_SCOPE_TEXT, RISK_CANCEL_TEXT, RISK_CONFIRM_TEXT,
} from '../../services/chat_policy.js';

/** 从一句话里挑出首个 Windows 绝对路径（.docx/.pdf/.txt），供本机服务代读。
 *  浏览器页面受沙箱限制读不了磁盘路径，所以这里只负责识别，读取交给后端
 *  /api/extract?source_path=；路径不存在或类型不支持时后端会明确报错。 */
function detectLocalPath(text) {
  const match = String(text || '').match(/[A-Za-z]:[\\/][^\n"'，。；：、<>|?*]*?\.(docx|pdf|txt)/i);
  return match ? match[0] : null;
}

/** 对话式入口：意图由 /api/chat/intent 只做分类，动作全部调用既有接口。
 *  助手回复只允许两类内容：固定话术常量，或接口返回的数据（卡片）；
 *  不把模型输出当事实，证据仍在详情页查看。 */
export function useChat({ mode, force, projectId, file, text }) {
  const messages = ref([]); // {id, role, text, card, via}
  const chatBusy = ref(false); // 意图识别 / 动作分发进行中
  const actionBusy = ref(''); // generate / delete 等按钮级忙碌状态
  const activeJob = ref(null); // 当前对话关联的解析任务（job.public()）
  let sequence = 0, pollTimer = null, disposed = false, pendingVia = '';

  function push(role, textValue, card = null, via = '') {
    // 本轮意图的判定来源只标在紧随其后的第一条助手回复上，便于用户核对分流。
    const label = role === 'assistant' && !via ? pendingVia : via;
    if (role === 'assistant') pendingVia = '';
    const message = { id: ++sequence, role, text: textValue, card, via: label };
    messages.value.push(message);
    return message.id;
  }
  function patchCard(id, card) {
    const message = messages.value.find(item => item.id === id);
    if (message) message.card = card;
  }
  function patchText(id, textValue, via = '') {
    const message = messages.value.find(item => item.id === id);
    if (!message) return;
    message.text = textValue;
    // 保留原有分流标注，只在后面追加表达层来源，便于用户区分「规则判定」与「模型总结」。
    if (via) message.via = message.via ? message.via + ' · ' + via : via;
  }
  function lastUserText() {
    for (let i = messages.value.length - 1; i >= 0; i -= 1) {
      if (messages.value[i].role === 'user') return messages.value[i].text;
    }
    return '';
  }
  /** 表达层：让后端基于已算好的结果做解释性总结。
   *  只接收数据文本、不产出新事实；失败或校验不通过时后端返回固定话术，
   *  这里再兜一层 catch，保证结果卡片永远不受影响。 */
  async function explain(id, intent, context) {
    try {
      const data = await api('/api/chat/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ intent, question: lastUserText(), context }),
      });
      if (data?.answer && data.matched_by === 'llm') patchText(id, data.answer, '模型总结');
    } catch { /* 表达层失败时保留固定话术，不影响结果卡片 */ }
  }
  function scrollTail() {
    requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }));
  }

  async function send(raw) {
    if (chatBusy.value) return;
    const content = (raw ?? text.value ?? '').trim();
    if (!content && !file.value) return;
    chatBusy.value = true;
    try {
      if (file.value) { push('user', '解析上传的报告：' + file.value.name); }
      else {
        push('user', content); text.value = '';
        // 与后端同口径的输入边界：超长指令不发送、不执行，直接给固定提示。
        if (content.length > MAX_CHAT_TEXT_CHARS) {
          push('assistant', INPUT_LIMIT_TEXT); return;
        }
      }
      scrollTail();
      // 带文件时意图明确（解析报告），跳过分类直接执行；纯文字先问后端分类。
      if (file.value) return await runExtract('');
      const intent = await api('/api/chat/intent', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: content }),
      });
      pendingVia = intent.matched_by || '';
      // 策略由后端统一下发：只读直执行、缺对象反问、越界拒答；旧后端没有
      // policy 时照旧分发，行为不回退。
      const policy = intent.policy || null;
      if (policy?.action === 'refuse') return push('assistant', OUT_OF_SCOPE_TEXT);
      if (policy?.action === 'ask') return push('assistant', ASK_TARGET_TEXT);
      // 高风险动作（生成 / 删除）先出确认卡，不直接产生副作用。
      if (policy?.risk === 'high' && policy?.action === 'execute' && await askConfirm(intent.intent)) return;
      await dispatch(intent.intent, content, intent.target);
    } catch (err) { push('assistant', '请求失败，未执行任何操作：' + err.message); }
    finally { chatBusy.value = false; scrollTail(); }
  }

  async function dispatch(intent, content = '', target = null) {
    switch (intent) {
      // 文字草稿在 push 用户消息时已清空，解析内容必须显式带下去。
      case 'extract': return await runExtract(content, target);
      case 'list_jobs': return await showJobs();
      case 'result': if (await ensureJob()) await showSummary(activeJob.value);
        return;
      case 'missing': if (await ensureJob()) await showReview();
        return;
      // 高风险动作统一走确认卡：即使旧后端没给 policy，也不会直接执行。
      case 'generate': return await askConfirm('generate');
      case 'download': if (await ensureJob()) push('assistant', '选择要下载的文件：', { type: 'downloads', job: activeJob.value });
        return;
      case 'delete': return await askConfirm('delete');
      case 'help': return push('assistant', HELP_TEXT);
      default: return push('assistant', OUT_OF_SCOPE_TEXT);
    }
  }

  /** 高风险动作先确认：卡片里带上动作、对象摘要与取消；对象在出卡时就锁定为
   *  快照，避免用户确认前被别的指令改掉 activeJob 而执行到另一条记录上。
   *  jobArg 用于卡片上点「生成」这类已指明对象的入口。 */
  async function askConfirm(intent, jobArg = null) {
    if (!RISK_CONFIRM_TEXT[intent]) return false;
    if (jobArg) activeJob.value = jobArg;
    if (!(await ensureJob())) return true;
    const job = activeJob.value;
    const summary = job.summary || {};
    const count = '字段有值 ' + (summary.fields_with_value || 0) + '，高风险项 ' + (summary.high || 0);
    const action = intent === 'delete' ? '删除解析' : '生成招标文件';
    push('assistant', '确认对「' + (job.filename || job.id) + '」' + action + '？\n' + count + '\n' + RISK_CONFIRM_TEXT[intent],
      { type: 'confirm-risk', intent, job });
    scrollTail();
    return true;
  }

  async function runExtract(contentArg, targetArg = null) {
    const content = (contentArg ?? text.value ?? '').trim();
    // 槽位以后端 target 为准；旧后端不带 target 时用本地识别兜底，行为不回退。
    let target = targetArg;
    if (!target && !file.value) {
      const localPath = detectLocalPath(content);
      if (localPath) target = { type: 'path', value: localPath };
    }
    // 路径直达：交给本机服务代读磁盘文件，不再把整句当正文。
    if (target?.type === 'path' && !file.value) {
      const base = target.value.split(/[\\/]/).pop() || '需求文件.docx';
      const query = new URLSearchParams({
        source_path: target.value,
        filename: base,
        use_llm: mode.value === 'full' ? '1' : '0',
        allow_non_tender: force.value ? '1' : '0',
      });
      if (projectId.value) query.set('project_id', projectId.value);
      try {
        const job = await api('/api/extract?' + query, { method: 'POST' });
        activeJob.value = job;
        push('assistant', '已读取本地文件「' + job.filename + '」并创建解析任务：', { type: 'progress', job });
        pollJob(job.id);
      } catch (err) {
        push('assistant', '无法解析该路径：' + err.message + '。也可以把文件直接拖进输入区。');
      }
      return;
    }
    // 要解析但没说清对象：反问，不把整句指令当成几十字的「需求正文」创建任务。
    if (!file.value && !target && content.length < 30) {
      push('assistant', ASK_TARGET_TEXT);
      return;
    }
    if (!file.value && !content) { push('assistant', '请粘贴采购需求文字，或把报告拖进上面的输入区。'); return; }
    const bodyText = target?.type === 'text' ? target.value : content;
    const input = file.value || new File([bodyText], '文字采购需求.txt', { type: 'text/plain;charset=utf-8' });
    if (input.size > 50 * 1024 * 1024) { push('assistant', '需求内容超过 50MB 上限。'); return; }
    // 与原「开始解析」同一套参数与二进制上传，证据链不变。
    const query = new URLSearchParams({ filename: input.name, use_llm: mode.value === 'full' ? '1' : '0', allow_non_tender: force.value ? '1' : '0' });
    if (projectId.value) query.set('project_id', projectId.value);
    const job = await api('/api/extract?' + query, { method: 'POST', body: input });
    activeJob.value = job;
    push('assistant', '已创建解析任务，开始处理：', { type: 'progress', job });
    pollJob(job.id);
  }

  function pollJob(id) {
    clearInterval(pollTimer);
    const tick = async () => {
      try {
        const job = await api('/api/jobs/' + encodeURIComponent(id));
        if (disposed) return;
        activeJob.value = job;
        const last = messages.value.at(-1);
        if (last?.card?.type === 'progress') last.card = { type: 'progress', job };
        if (['queued', 'running'].includes(job.status)) return;
        clearInterval(pollTimer);
        if (job.status === 'error') push('assistant', '解析失败：' + (job.error || '未知错误'));
        else if (job.status === 'skipped') push('assistant', '未执行抽取：' + (job.summary?.reject_reason || '该文件未被识别为采购需求文件。如确认文件类型无误，可勾选「强制抽取」后重试。'));
        else {
          const id = push('assistant', '解析完成。', { type: 'summary', job });
          explain(id, 'result', {
            filename: job.filename, status: job.status, elapsed: job.elapsed,
            has_result: job.has_result, summary: job.summary,
          });
        }
      } catch (err) { if (!disposed) { clearInterval(pollTimer); push('assistant', '查询任务失败：' + err.message); } }
      scrollTail();
    };
    tick();
    pollTimer = setInterval(tick, 1200);
  }

  async function showJobs() {
    const rows = ((await api('/api/jobs')).jobs || []).filter(item => item.kind === 'extract');
    if (!rows.length) { push('assistant', '还没有解析记录，粘贴需求文字或上传报告即可开始。'); return; }
    const shown = rows.slice(0, 10);
    const id = push('assistant', '共 ' + rows.length + ' 条解析记录：', { type: 'jobs', jobs: shown });
    explain(id, 'jobs', {
      total: rows.length,
      jobs: shown.map(item => ({ filename: item.filename, status: item.status, elapsed: item.elapsed })),
    });
  }

  /**  job 类指令先确定操作对象：优先当前任务，否则自动关联最近一条已结束记录。 */
  async function ensureJob() {
    if (activeJob.value && !['queued', 'running'].includes(activeJob.value.status)) return true;
    try {
      const rows = ((await api('/api/jobs')).jobs || [])
        .filter(item => item.kind === 'extract' && ['done', 'skipped', 'error'].includes(item.status));
      if (rows.length) {
        activeJob.value = rows[0];
        push('assistant', '已关联最近的解析「' + (rows[0].filename || rows[0].id) + '」。', { type: 'summary', job: rows[0] });
        return true;
      }
    } catch { /* 列表不可用时按无任务提示 */ }
    push('assistant', '还没有可用的解析记录，先粘贴需求文字或上传报告解析一份。');
    return false;
  }

  async function showSummary(job) {
    const id = push('assistant', '「' + (job.filename || job.id) + '」的解析结果：', { type: 'summary', job });
    scrollTail();
    explain(id, 'result', {
      filename: job.filename, status: job.status, elapsed: job.elapsed,
      has_result: job.has_result, summary: job.summary,
    });
  }

  async function showReview() {
    actionBusy.value = 'review';
    try {
      const data = await api('/api/jobs/' + encodeURIComponent(activeJob.value.id) + '/result');
      const items = [...(data.review || [])].sort((a, b) => (({ high: 0, medium: 1, low: 2 })[a.priority] ?? 9) - (({ high: 0, medium: 1, low: 2 })[b.priority] ?? 9));
      if (!items.length) { push('assistant', '「' + (activeJob.value.filename || '') + '」没有需要人工复核的项。'); return; }
      const id = push('assistant', '待人工复核 ' + items.length + ' 项，请结合原文确认：', { type: 'review', job: activeJob.value, items });
      explain(id, 'missing', {
        filename: activeJob.value.filename,
        total: items.length,
        items: items.slice(0, 10).map(item => ({
          field: item.field, label: item.label, priority: item.priority, issue: item.issue,
        })),
      });
    } catch (err) { push('assistant', err.message); }
    finally { actionBusy.value = ''; }
    scrollTail();
  }

  async function doGenerate(id) {
    actionBusy.value = 'generate';
    try { await download('/api/jobs/' + encodeURIComponent(id) + '/generate', '招标文件.docx'); push('assistant', '招标文件已生成并开始下载。'); }
    catch (err) { push('assistant', '生成失败：' + err.message); }
    finally { actionBusy.value = ''; }
  }

  async function doDelete(job) {
    actionBusy.value = 'delete';
    try {
      await api('/api/jobs/' + encodeURIComponent(job.id), { method: 'DELETE' });
      if (activeJob.value?.id === job.id) activeJob.value = null;
      push('assistant', '已删除解析「' + (job.filename || job.id) + '」。');
    } catch (err) { push('assistant', '删除失败：' + err.message); }
    finally { actionBusy.value = ''; }
    scrollTail();
  }

  /** 卡片按钮统一入口：ChatStream 只发事件，动作集中在这里执行。 */
  async function act(op, arg) {
    if (op === 'pick-job') {
      activeJob.value = arg;
      push('assistant', '已选择解析「' + (arg.filename || arg.id) + '」。');
      return scrollTail();
    }
    // 高风险动作的按钮同样只出确认卡，不直接调用接口。
    if (op === 'generate') return askConfirm('generate', arg);
    if (op === 'missing') return showReview();
    if (op === 'confirm-yes') {
      if (arg?.intent === 'delete') return doDelete(arg.job);
      if (arg?.intent === 'generate') return doGenerate(arg.job?.id || activeJob.value?.id);
      return scrollTail();
    }
    if (op === 'confirm-no') { push('assistant', RISK_CANCEL_TEXT); return scrollTail(); }
    if (op === 'refresh-jobs') return showJobs();
  }

  onBeforeUnmount(() => { disposed = true; clearInterval(pollTimer); });
  window.addEventListener('pagehide', () => { disposed = true; clearInterval(pollTimer); }, { once: true });

  return { messages, chatBusy, actionBusy, activeJob, send, act };
}
