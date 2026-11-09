import { ref, reactive, onMounted, onBeforeUnmount } from 'vue';
import { api, post } from '../../services/api.js';
import { dialog } from '../../utils/dialog.js';

/** TemplatesPage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  const templates = ref([]), pendingFile = ref(null), selected = ref(null), deleting = ref(null), busy = ref(false);
  const notice = reactive({ text: "", kind: "" }), rename = reactive({ id: "", name: "" });
  const uploadForm = reactive({ name: "", category: "service", method: "open" });
  const previewName = ref(""), previewMessage = ref(""), previewSections = ref([]);
  const category = { goods: "货物", service: "服务", works: "工程", other: "其他" };
  const method = { open: "公开招标", invite: "邀请招标", negotiation: "竞争性磋商", inquiry: "询价", competitive: "竞争性谈判", single: "单一来源", other: "其他" };
  let previewSequence = 0, loadSequence = 0;
  function notify(text, kind = "") { Object.assign(notice, { text, kind }); }
  async function load() {
    const sequence = ++loadSequence;
    try {
      const data = await api("/api/templates");
      if (sequence !== loadSequence) return;
      templates.value = data.templates || [];
      const count = templates.value.reduce((sum, item) => sum + (item.unmapped || []).length, 0);
      notify(count ? "有 " + count + " 个占位符没有对应字段，可在填写工作台绑定。" : "", count ? "warn" : "");
    } catch (err) { if (sequence === loadSequence) notify("加载模板库失败：" + err.message, "error"); }
  }
  async function mutate(fn) {
    if (busy.value) return;
    busy.value = true;
    try { await fn(); } catch (err) { notify(err.message, "error"); } finally { busy.value = false; }
  }
  function selectFile(event) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    if (!/\.docx$/i.test(file.name)) { notify("只支持 .docx 模板；.doc 请先用 Word 另存为 .docx。", "error"); return; }
    if (!file.size || file.size > 50 * 1024 * 1024) { notify(!file.size ? "模板文件为空" : "文件超过 50MB 上限", "error"); return; }
    pendingFile.value = file; uploadForm.name = file.name.replace(/\.docx$/i, ""); dialog("dlgUpload");
  }
  async function upload() {
    if (!pendingFile.value) return;
    await mutate(async () => {
      const query = new URLSearchParams({ filename: pendingFile.value.name, ...uploadForm });
      const info = await api("/api/templates?" + query, { method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: pendingFile.value });
      dialog("dlgUpload", false); await load(); notify("已上传「" + info.name + "」" + (info.unmapped?.length ? "，其中 " + info.unmapped.length + " 个占位符没有对应字段。" : "，全部占位符都能对应字段。"), info.unmapped?.length ? "warn" : "");
    });
  }
  async function setDefault(template) { await mutate(async () => { await post("/api/templates/" + encodeURIComponent(template.id) + "/default"); await load(); notify("已将「" + template.name + "」设为默认模板。"); }); }
  function openRename(template) { Object.assign(rename, { id: template.id, name: template.name }); dialog("dlgRename"); }
  async function saveRename() {
    if (!rename.name.trim()) { notify("模板名称不能为空。", "error"); return; }
    await mutate(async () => { await post("/api/templates/" + encodeURIComponent(rename.id) + "/rename", { name: rename.name.trim() }); dialog("dlgRename", false); await load(); notify("已重命名。"); });
  }
  function askDelete(template) { deleting.value = template; dialog("dlgConfirm"); }
  async function remove() { if (deleting.value) await mutate(async () => { await api("/api/templates/" + encodeURIComponent(deleting.value.id), { method: "DELETE" }); dialog("dlgConfirm", false); await load(); notify("模板已删除。"); }); }
  function showDetail(template) { selected.value = template; dialog("dlgDetail"); }
  function invalidatePreview() { previewSequence += 1; }
  async function showPreview(template) {
    const sequence = ++previewSequence;
    previewName.value = template.name; previewSections.value = []; previewMessage.value = "正在解析模板结构…"; dialog("dlgPreview");
    try {
      const data = await api("/api/templates/" + encodeURIComponent(template.id) + "/preview");
      if (sequence !== previewSequence) return;
      previewSections.value = data.sections || []; previewMessage.value = "";
    } catch (err) { if (sequence === previewSequence) previewMessage.value = "预览失败：" + err.message; }
  }
  onMounted(load); onBeforeUnmount(invalidatePreview);
  return { templates, pendingFile, selected, deleting, busy, notice, rename, uploadForm, previewName, previewMessage, previewSections, category, method, load, selectFile, upload, setDefault, openRename, saveRename, askDelete, remove, showDetail, showPreview, invalidatePreview, dialog };

}
