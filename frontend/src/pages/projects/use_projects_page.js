import { ref, reactive, computed, onMounted } from 'vue';
import { api, post } from '../../services/api.js';
import { download } from '../../services/download.js';
import { dialog } from '../../utils/dialog.js';

/** ProjectsPage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  const projects = ref([]), detail = ref(null), status = ref("active"), error = ref(""), busy = ref(false);
  const draft = reactive({ name: "", note: "" }), rename = reactive({ id: "", name: "" });
  const pendingDelete = ref(null), deleteError = ref('');
  let loadSequence = 0, detailSequence = 0;
  const filters = [["active", "进行中"], ["archived", "已归档"], ["", "全部"]];
  const doneCount = computed(() => (detail.value?.jobs || []).filter(job => job.status === "done").length);
  async function load() {
    const sequence = ++loadSequence;
    try {
      const data = await api("/api/projects" + (status.value ? "?status=" + status.value : ""));
      if (sequence === loadSequence) projects.value = data.projects || [];
    } catch (err) { if (sequence === loadSequence) error.value = err.message; }
  }
  async function openDetail(id) {
    const sequence = ++detailSequence;
    try { const data = await api("/api/projects/" + encodeURIComponent(id)); if (sequence === detailSequence) detail.value = data; }
    catch (err) { if (sequence === detailSequence) error.value = err.message; }
  }
  function back() { detailSequence += 1; detail.value = null; load(); }
  function selectStatus(value) { status.value = value; load(); }
  function openNew() { draft.name = ""; draft.note = ""; dialog("dlg-new"); }
  function openRename(project) { Object.assign(rename, { id: project.id, name: project.name }); dialog("dlg-rename"); }
  async function mutate(fn) {
    if (busy.value) return;
    busy.value = true; error.value = "";
    try { await fn(); await load(); } catch (err) { error.value = err.message; }
    finally { busy.value = false; }
  }
  async function create() {
    if (!draft.name.trim()) { error.value = "请填写项目名称"; return; }
    await mutate(async () => { await post("/api/projects", { name: draft.name.trim(), note: draft.note.trim() }); dialog("dlg-new", false); });
  }
  async function saveRename() {
    if (!rename.name.trim()) { error.value = "项目名称不能为空"; return; }
    await mutate(async () => { await post("/api/projects/" + encodeURIComponent(rename.id) + "/rename", { name: rename.name.trim() }); dialog("dlg-rename", false); });
  }
  async function action(project, operation) {
    // 删除先展示页面中央的确认弹窗，确认之前不发送任何变更请求。
    if (operation === 'delete') { pendingDelete.value = project; deleteError.value = ''; return; }
    await mutate(() => post("/api/projects/" + encodeURIComponent(project.id) + "/" + operation));
  }
  async function confirmDelete() {
    if (!pendingDelete.value || busy.value) return;
    busy.value = true; deleteError.value = '';
    try {
      await post('/api/projects/' + encodeURIComponent(pendingDelete.value.id) + '/delete');
      pendingDelete.value = null; await load();
    } catch (err) { deleteError.value = err.message; }
    finally { busy.value = false; }
  }
  async function exportZip() { await mutate(() => download("/api/projects/" + encodeURIComponent(detail.value.id) + "/export", "项目导出.zip")); }
  function fmtTime(value) { return value ? String(value).replace("T", " ") : "—"; }
  function jobTarget(job) { return job.kind === "compare" ? "/compare.html?job=" + job.id : job.status === "done" ? "/fill.html?job=" + job.id : "/?job=" + job.id; }
  function jobTag(value) { return ({ done: ["完成", "t-ok"], running: ["进行中", "t-run"], queued: ["排队中", "t-run"], skipped: ["已跳过", "t-warn"], error: ["失败", "t-bad"] })[value] || [value || "未知", "t-mute"]; }
  onMounted(() => { load(); const id = new URLSearchParams(location.search).get("id"); if (id) openDetail(id); });
  return { pendingDelete, deleteError, confirmDelete, projects, detail, status, error, busy, draft, rename, filters, doneCount, selectStatus, openNew, openRename, openDetail, back, create, saveRename, action, exportZip, fmtTime, jobTarget, jobTag, dialog };

}
