import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue';
import { api, post } from '../../services/api.js';
import { download, saveBlob } from '../../services/download.js';
import { fmtSize } from '../../utils/format_size.js';
import { useJobPolling } from '../../composables/use_job_polling.js';
import { formatters as reportFormatters } from '../../utils/formatters.js';

/** IndexPage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  // 用查询参数区分列表、新建与详情，保留现有入口和可复制链接，不引入路由依赖。
  const query = new URLSearchParams(location.search);
  const view = ref(query.has('job') ? 'detail' : query.get('view') === 'new' ? 'new' : 'list');
  // 与侧栏名称一致，并区分同一 HTML 入口下的三个页面，方便浏览器标签识别。
  document.title = ({ list: '需求解析', new: '新建解析', detail: '需求详情' })[view.value];
  const deleting = ref(false), deleteOpen = ref(false), deleteError = ref('');
  const text = ref(''), jobs = ref([]), listLoading = ref(false), filter = ref('all'), search = ref('');
  const filters = [['all', '全部'], ['running', '解析中'], ['done', '已完成'], ['error', '失败 / 已跳过']];
  const filteredJobs = computed(() => jobs.value.filter(item =>
    (filter.value === 'all' || (filter.value === 'running' ? ['queued', 'running'].includes(item.status) : filter.value === 'error' ? ['error', 'skipped'].includes(item.status) : item.status === filter.value)) &&
    (!search.value.trim() || (item.filename || '').toLowerCase().includes(search.value.trim().toLowerCase()))));
  const canSubmit = computed(() => !!file.value || !!text.value.trim());
  // 与三类筛选共用蓝 / 绿 / 红语义；跳过保留原文案，归入未完成的红色组。
  const jobLabels = { done: ['已完成', 't-ok'], queued: ['排队中', 't-info'], running: ['解析中', 't-info'], error: ['解析失败', 't-bad'], skipped: ['已跳过', 't-bad'] };
  let listTimer, disposed = false;
  const file = ref(null), dragging = ref(false), mode = ref("full"), force = ref(false), projectId = ref(""), projects = ref([]);
  const inputElement = ref(null);
  let inputObserver, dragDepth = 0;
  function resizeInput() {
    // 先还原高度再测量，删除文字也能收回；宽度由容器决定，超过 250px 只滚动正文。
    const element = inputElement.value;
    if (!element) return;
    element.style.height = '0px';
    const height = Math.max(72, Math.min(250, element.scrollHeight));
    element.style.height = height + 'px';
    element.style.overflowY = element.scrollHeight > height ? 'auto' : 'hidden';
  }
  watch(text, resizeInput, { flush: 'post' });
  function resetDrag() { dragDepth = 0; dragging.value = false; }
  function isFileDrag(event) { return Array.from(event.dataTransfer?.types || []).includes('Files'); }
  function enterDrag(event) {
    // enter/leave 在子元素之间也会触发，计数避免鼠标经过文字、按钮时提示闪烁。
    if (!isFileDrag(event)) return;
    event.preventDefault(); dragDepth += 1; dragging.value = true;
  }
  function overDrag(event) {
    if (!isFileDrag(event)) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'copy'; dragging.value = true;
  }
  function leaveDrag(event) {
    if (!isFileDrag(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) dragging.value = false;
  }
  function dropFile(event) {
    if (!isFileDrag(event)) return;
    event.preventDefault(); resetDrag(); setFile(event.dataTransfer.files[0]);
  }
  const error = ref(""), busy = ref(false), job = ref(null), result = ref(null), report = ref(""), pane = ref("fields");
  const health = ref({ available: false, checking: true }), bidResult = ref(null), riskResult = ref(null), actionBusy = ref("");
  const suggestionTab = ref("common"), formatters = reportFormatters;
  const suggestionTabs = [["common", "常用"], ["build", "生成"], ["check", "核对"], ["keep", "归档"]];
  const suggestionList = computed(() => ({ common: ["抽取结构化字段并附原文证据", "查看缺失项与待人工确认的字段", "生成可编辑的招标文件 .docx", "核对投标响应与资格要求的差距", "按项目归档多轮解析结果", "导出 JSON、审核报告与平台草稿"], build: ["把标的清单填进招标文件表格", "逐条标注 ★ ▲ 实质性条款", "补齐商务要求与编制规范默认值", "导出 PDF 与平台草稿"], check: ["比对招标要求与投标响应", "标记不满足与部分满足的条目", "检查模板占位符覆盖情况", "复核低置信与推断字段"], keep: ["按采购项目归档任务", "恢复已归档项目继续编辑", "在企业档案里复用资质与业绩"] })[suggestionTab.value]);
  const panes = [["fields", "字段"], ["review", "待复核"], ["template", "模板映射"], ["report", "审核报告"]];
  const statusLabels = { ok: ["正常", "t-ok"], low_confidence: ["低置信", "t-low"], inferred: ["推断", "t-info"], missing: ["缺失", "t-low"], invalid: ["无效", "t-bad"], conflict: ["冲突", "t-bad"] };
  const sourceLabels = { label: "标签", table: "表格", regex: "正则", computed: "计算", inferred: "推断", llm: "模型", "rule+llm": "规则+模型", none: "无" };
  const priorityLabels = { high: ["高", "t-bad"], medium: ["中", "t-warn"], low: ["低", "t-ok"] };
  const reviewItems = computed(() => [...(result.value?.review || [])].sort((a, b) => (({ high: 0, medium: 1, low: 2 })[a.priority] ?? 9) - (({ high: 0, medium: 1, low: 2 })[b.priority] ?? 9)));
  const healthText = computed(() => health.value.checking ? "检测模型中…" : health.value.available ? "模型就绪 · " + (health.value.model || health.value.provider || "") : "模型不可用 · 可使用仅规则模式");
  const stats = computed(() => { const summary = job.value?.summary || {}; return [["字段有值", summary.fields_with_value || 0], ["模板覆盖", (summary.templates_filled || 0) + " / " + (summary.templates_total || 0)], ["高风险复核", summary.high || 0], ["中风险复核", summary.medium || 0], ["用时", (job.value?.elapsed || 0) + "s"]]; });
  const resultMeta = computed(() => {
    const summary = job.value?.summary || {}, review = summary.review || {};
    return "文件：" + (job.value?.filename || "") + " · " + (summary.model ? "模型：" + summary.provider + " / " + summary.model : "模型：未使用") + (review.enabled ? review.available ? " · 第三方复核：" + review.provider + "/" + review.model + "（通过 " + review.supported + "，驳回 " + review.rejected + "，改为 " + review.replaced + "）" : " · 第三方复核未生效" + (review.error ? "：" + review.error : "") : "");
  });
  let resultSequence = 0;
  async function loadResult(value) {
    const sequence = ++resultSequence;
    const [data, markdown] = await Promise.all([api("/api/jobs/" + value.id + "/result"), api("/api/jobs/" + value.id + "/report")]);
    if (sequence !== resultSequence) return;
    job.value = value; result.value = data; report.value = markdown.markdown || "";
  }
  const { progress, start, stop } = useJobPolling(loadResult, text => { error.value = text; });
  // 详情始终保留最后阶段，失败时保留真实百分比，只有结果加载完成才到 100%。
  const detailJob = computed(() => progress.job || job.value);
  const percent = computed(() => result.value ? 100 : progress.percent);
  watch(result, value => document.body.classList.toggle("has-result", !!value));
  async function loadHealth() { health.value = { ...health.value, checking: true }; try { health.value = await api("/api/health"); } catch (err) { health.value = { available: false, reason: err.message }; } }
  function setFile(value) {
    if (!value) return;
    if (!/\.(docx|pdf|txt)$/i.test(value.name)) { error.value = "仅支持 .docx / .pdf / .txt 文件"; return; }
    if (!value.size || value.size > 50 * 1024 * 1024) { error.value = !value.size ? "文件内容为空" : "文件超过 50MB 上限"; return; }
    file.value = value; error.value = "";
  }
  function saveProject() { try { if (projectId.value) localStorage.setItem("tender.currentProject", projectId.value); else localStorage.removeItem("tender.currentProject"); } catch { /* 无持久化权限时仍允许本次选择。 */ } }
  async function startExtraction(retryForce) {
    if (!canSubmit.value) { error.value = "请输入采购需求或选择一份需求报告"; return; }
    if (busy.value || progress.running) return;
    stop(); resultSequence += 1; result.value = null; bidResult.value = null; riskResult.value = null; error.value = ""; busy.value = true;
    try {
      // 文字作为 UTF-8 TXT 原文上传，复用相同的证据核验；原文不放进 URL。
      const input = file.value || new File([text.value.trim()], '文字采购需求.txt', { type: 'text/plain;charset=utf-8' });
      if (input.size > 50 * 1024 * 1024) throw new Error('需求内容超过 50MB 上限');
      const query = new URLSearchParams({ filename: input.name, use_llm: mode.value === "full" ? "1" : "0", allow_non_tender: force.value || retryForce ? "1" : "0" });
      if (projectId.value) query.set("project_id", projectId.value);
      const value = await api("/api/extract?" + query, { method: "POST", body: input });
      // 服务端创建任务成功后跳转；刷新详情仍能恢复后台任务。
      location.assign('/?job=' + encodeURIComponent(value.id));
    } catch (err) { error.value = err.message; } finally { busy.value = false; }
  }
  async function openJob(id) {
    try { const value = await api("/api/jobs/" + encodeURIComponent(id)); job.value = value; progress.job = value; progress.percent = Number(value.progress) || 0; if (["queued", "running"].includes(value.status)) await start(value); else if (value.status === "error") error.value = value.error || "该任务执行失败"; else await loadResult(value); }
    catch (err) { error.value = "载入任务失败：" + err.message; }
  }
  async function runAction(kind) {
    if (!job.value || actionBusy.value) return;
    actionBusy.value = kind; error.value = "";
    try { const data = await post("/api/jobs/" + job.value.id + "/" + kind); if (kind === "bid") bidResult.value = data; else riskResult.value = data; }
    catch (err) { error.value = err.message; } finally { actionBusy.value = ""; }
  }
  async function exportPdf() {
    actionBusy.value = "pdf";
    try { await download("/api/jobs/" + job.value.id + "/export/pdf", "招标文件.pdf"); } catch (err) { error.value = err.message; } finally { actionBusy.value = ""; }
  }
  function downloadBid() { saveBlob(new Blob([bidResult.value.markdown || ""], { type: "text/markdown;charset=utf-8" }), (bidResult.value.summary?.project_name || "投标书") + "-投标书初稿.md"); }
  function downloadGraph() { saveBlob(new Blob([riskResult.value.turtle || ""], { type: "text/turtle;charset=utf-8" }), (riskResult.value.project_name || "图谱") + ".ttl"); }
  async function loadJobs() {
    // 仅显示需求抽取任务；有运行中任务时自动刷新卡片，离开页面即取消。
    clearTimeout(listTimer); listLoading.value = true;
    try {
      const rows = ((await api('/api/jobs')).jobs || []).filter(item => item.kind === 'extract');
      // 列表记录来自数据库；运行中阶段以内存任务接口为准，完成耗时取真实时间戳。
      const values = await Promise.all(rows.map(async item => {
        if (['queued', 'running'].includes(item.status)) return { ...item, ...await api('/api/jobs/' + encodeURIComponent(item.id)) };
        const duration = Date.parse(item.finished_at) - Date.parse(item.created_at);
        return { ...item, elapsed: item.elapsed ?? (Number.isFinite(duration) ? Math.max(0, duration / 1000).toFixed(1) : '—') };
      }));
      if (!disposed) jobs.value = values;
    }
    catch (err) { error.value = '载入需求失败：' + err.message; }
    finally { listLoading.value = false; }
    if (!disposed && view.value === 'list' && jobs.value.some(item => ['queued', 'running'].includes(item.status))) listTimer = setTimeout(loadJobs, 3000);
  }
  function projectName(id) { return projects.value.find(item => item.id === id)?.name || '不归属项目'; }
  function openDelete() { deleteError.value = ''; deleteOpen.value = true; }
  async function deleteRequirement() {
    if (!job.value || deleting.value) return;
    deleting.value = true; deleteError.value = '';
    try {
      await api('/api/jobs/' + encodeURIComponent(job.value.id), { method: 'DELETE' });
      stop(); location.assign('/');
    } catch (err) { deleteError.value = err.message; }
    finally { deleting.value = false; }
  }
  onBeforeUnmount(() => {
    disposed = true; clearTimeout(listTimer); document.body.classList.remove('has-result');
    inputObserver?.disconnect();
    for (const event of ['dragend', 'drop', 'blur']) window.removeEventListener(event, resetDrag);
  });
  onMounted(async () => {
    // 宽度变化也会改变换行数；只在宽度改变时重测，避免高度通知引起观察器循环。
    if (inputElement.value) {
      resizeInput();
      let previousWidth = inputElement.value.clientWidth;
      inputObserver = new ResizeObserver(() => {
        const width = inputElement.value?.clientWidth;
        if (width !== previousWidth) { previousWidth = width; resizeInput(); }
      });
      inputObserver.observe(inputElement.value);
      for (const event of ['dragend', 'drop', 'blur']) window.addEventListener(event, resetDrag);
    }
    loadHealth();
    try {
      projects.value = (await api("/api/projects?status=active")).projects || [];
      const saved = localStorage.getItem("tender.currentProject") || ""; projectId.value = projects.value.some(value => value.id === saved) ? saved : "";
    } catch { /* 项目列表或浏览器存储不可用不阻断上传。 */ }
    const selectedProject = query.get('project_id');
    if (selectedProject && projects.value.some(item => item.id === selectedProject)) projectId.value = selectedProject;
    const id = query.get('job'); if (id) { openJob(id); return; }
    if (view.value === 'list') loadJobs();
  });
  return { deleting, deleteOpen, deleteError, openDelete, deleteRequirement, inputElement, enterDrag, overDrag, leaveDrag, dropFile, view, text, jobs, listLoading, filter, search, filters, filteredJobs, canSubmit, jobLabels, detailJob, percent, loadJobs, projectName, file, dragging, mode, force, projectId, projects, error, busy, job, result, report, pane, health, healthText, bidResult, riskResult, actionBusy, suggestionTab, suggestionTabs, suggestionList, panes, statusLabels, sourceLabels, priorityLabels, reviewItems, stats, resultMeta, formatters, progress, fmtSize, loadHealth, setFile, saveProject, startExtraction, runAction, exportPdf, downloadBid, downloadGraph };

}
