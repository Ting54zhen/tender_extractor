import { onBeforeUnmount, ref } from 'vue';
import { api } from '../../services/api.js';
import { download } from '../../services/download.js';
import { HELP_TEXT, INPUT_LIMIT_TEXT, MAX_CHAT_TEXT_CHARS, OUT_OF_SCOPE_TEXT } from '../../services/chat_policy.js';

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
      await dispatch(intent.intent, content);
    } catch (err) { push('assistant', '请求失败，未执行任何操作：' + err.message); }
    finally { chatBusy.value = false; scrollTail(); }
  }

  async function dispatch(intent, content = '') {
    switch (intent) {
      // 文字草稿在 push 用户消息时已清空，解析内容必须显式带下去。
      case 'extract': return await runExtract(content);
      case 'list_jobs': return await showJobs();
      case 'result': if (await ensureJob()) await showSummary(activeJob.value);
        return;
      case 'missing': if (await ensureJob()) await showReview();
        return;
      case 'generate': if (await ensureJob()) await doGenerate(activeJob.value.id);
        return;
      case 'download': if (await ensureJob()) push('assistant', '选择要下载的文件：', { type: 'downloads', job: activeJob.value });
        return;
      case 'delete': if (await ensureJob()) push('assistant', '确认删除解析「' + (activeJob.value.filename || activeJob.value.id) + '」？记录与确认值会一并删除，不可恢复。', { type: 'confirm-delete', job: activeJob.value });
        return;
      case 'help': return push('assistant', HELP_TEXT);
      default: return push('assistant', OUT_OF_SCOPE_TEXT);
    }
  }

  async function runExtract(contentArg) {
    const content = (contentArg ?? text.value ?? '').trim();
    if (!file.value && !content) { push('assistant', '请粘贴采购需求文字，或把报告拖进上面的输入区。'); return; }
    const input = file.value || new File([content], '文字采购需求.txt', { type: 'text/plain;charset=utf-8' });
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
        else { push('assistant', '解析完成。', { type: 'summary', job }); }
      } catch (err) { if (!disposed) { clearInterval(pollTimer); push('assistant', '查询任务失败：' + err.message); } }
      scrollTail();
    };
    tick();
    pollTimer = setInterval(tick, 1200);
  }

  async function showJobs() {
    const rows = ((await api('/api/jobs')).jobs || []).filter(item => item.kind === 'extract');
    if (!rows.length) { push('assistant', '还没有解析记录，粘贴需求文字或上传报告即可开始。'); return; }
    push('assistant', '共 ' + rows.length + ' 条解析记录：', { type: 'jobs', jobs: rows.slice(0, 10) });
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
    push('assistant', '「' + (job.filename || job.id) + '」的解析结果：', { type: 'summary', job });
    scrollTail();
  }

  async function showReview() {
    actionBusy.value = 'review';
    try {
      const data = await api('/api/jobs/' + encodeURIComponent(activeJob.value.id) + '/result');
      const items = [...(data.review || [])].sort((a, b) => (({ high: 0, medium: 1, low: 2 })[a.priority] ?? 9) - (({ high: 0, medium: 1, low: 2 })[b.priority] ?? 9));
      if (!items.length) { push('assistant', '「' + (activeJob.value.filename || '') + '」没有需要人工复核的项。'); return; }
      push('assistant', '待人工复核 ' + items.length + ' 项，请结合原文确认：', { type: 'review', job: activeJob.value, items });
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
    if (op === 'generate') return doGenerate(arg?.id || activeJob.value?.id);
    if (op === 'missing') return showReview();
    if (op === 'delete-yes') return doDelete(arg);
    if (op === 'refresh-jobs') return showJobs();
  }

  onBeforeUnmount(() => { disposed = true; clearInterval(pollTimer); });
  window.addEventListener('pagehide', () => { disposed = true; clearInterval(pollTimer); }, { once: true });

  return { messages, chatBusy, actionBusy, activeJob, send, act };
}
