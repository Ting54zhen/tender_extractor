import { ref, reactive, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue';
import { api, post } from '../../services/api.js';
import { download } from '../../services/download.js';
import { readLocal, writeLocal } from '../../utils/storage.js';
import { dialog } from '../../utils/dialog.js';
import { onlyMissing as filterMissingSections } from '../../utils/preview.js';
import { statusOf } from '../../utils/field_status.js';

/** FillPage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  const jobId = new URLSearchParams(location.search).get("job") || "";
  const jobInfo = ref(null), baseGroups = ref([]), templates = ref([]), templateId = ref("");
  const customFields = ref([]), edits = reactive({}), suggestions = ref({}), activeGroup = ref(""), filter = ref("all");
  const loading = ref(true), scopeLoading = ref(false), generating = ref(false), suggesting = ref(false);
  const scopeState = reactive({ method: "", category: "", org_form: "" }), scopeAxes = ref({}), scopeData = ref(null), unmapped = ref([]);
  const notice = reactive({ text: "", kind: "" }), toastText = ref(""), deleting = ref(null), flashKey = ref("");
  const previewOpen = ref(false), preview = ref(null), previewLoading = ref(false), previewError = ref(""), onlyMissing = ref(false), missingMode = ref("mark");
  const customForm = reactive({ key: "", label: "", value: "", type: "text", target: "append", placeholder: "", options: "" });
  const axes = [{ key: "method", id: "scopeMethod", label: "采购方式" }, { key: "category", id: "scopeCategory", label: "业务类型" }, { key: "org_form", id: "scopeOrgForm", label: "招标组织形式" }];
  const filters = [["all", "全部"], ["todo", "待处理"], ["low", "低置信"], ["missing", "缺失"]];
  let scopeSequence = 0, previewSequence = 0, previewTimer = null, toastTimer = null, flashTimer = null, previewController = null;
  const storeKey = kind => "tender." + kind + "." + jobId;

  /** 切页是整页跳转，内存 edits 会重建，字段编辑不落盘就等于白填。
   *  建议值只是待确认的展示内容，暂存避免切页回来重新调用模型起草，
   *  采纳前不写抽取结果、不进成稿；暂存损坏时退回抽取值，不把脏数据带进成稿。 */
  // load() 里的异步赋值会先触发 payload watch；恢复完成前不允许把空 edits 写回存储。
  let editsReady = false;
  function persistEdits() { if (editsReady) writeLocal(storeKey("edits"), { ...edits }); }
  function restoreEdits() {
    const stored = readLocal(storeKey("edits"), {});
    if (stored && typeof stored === "object") {
      for (const [key, entry] of Object.entries(stored)) {
        if (entry && typeof entry === "object" && typeof entry.value === "string") {
          edits[key] = { value: entry.value, mode: String(entry.mode || "edited") };
        }
      }
    }
    editsReady = true;
  }
  function persistSuggestions() { writeLocal(storeKey("suggestions"), suggestions.value); }
  function restoreSuggestions() {
    const stored = readLocal(storeKey("suggestions"), {});
    suggestions.value = stored && typeof stored === "object" && !Array.isArray(stored) ? stored : {};
  }
  const scopeComplete = computed(() => axes.every(axis => !!scopeState[axis.key]));
  const ready = computed(() => !loading.value && !scopeLoading.value && scopeComplete.value && jobInfo.value?.status === "done" && !!scopeData.value?.complete);
  const currentTemplate = computed(() => templates.value.find(item => item.id === templateId.value));

  /** 人工字段与类型专属字段仅加入视图，不写进原始抽取结果。 */
  const allCustom = computed(() => customFields.value.concat((scopeData.value?.extra_fields || []).map(field => ({
    key: "scope:" + field.key, label: field.label, value: "", type: field.field_type || "text",
    target: "append", placeholder: "", options: field.options || [], hint: field.hint || "", scope: true
  }))));
  const groups = computed(() => {
    const list = baseGroups.value.filter(group => group.key !== "custom");
    if (!allCustom.value.length) return list;
    return list.concat({
      key: "custom", label: "自定义字段", items: allCustom.value.map(field => ({
        key: field.key, label: field.label, template: "", value: field.value || "", custom: true, cf: field
      }))
    });
  });
  const allItems = computed(() => groups.value.flatMap(group => group.items));
  const selectedGroup = computed(() => groups.value.find(group => group.key === activeGroup.value));
  const groupIndex = computed(() => groups.value.findIndex(group => group.key === activeGroup.value));
  const previousGroup = computed(() => groups.value[groupIndex.value - 1]);
  const nextGroup = computed(() => groups.value[groupIndex.value + 1]);
  const targetList = computed(() => allItems.value.find(item => item.key === "procurement_target_list")?.value || "");
  function valueOf(item) {
    // 必须读取代理对象的键，Vue 才会追踪「原先没有、后来新增」的编辑项；
    // 仅用 Object.prototype.hasOwnProperty.call 判断会漏掉这类响应式依赖。
    const edit = edits[item.key];
    return String(edit ? edit.value ?? "" : item.value ?? "");
  }
  function groupStats(group) {
    const items = group.items.filter(item => (item.template || item.custom) && !item.na);
    const filled = items.filter(item => valueOf(item).trim()).length;
    return { total: items.length, filled, missing: items.length - filled };
  }
  const progress = computed(() => {
    const stats = groupStats({ items: allItems.value });
    return { ...stats, percent: stats.total ? Math.round(stats.filled / stats.total * 100) : 0 };
  });
  const visibleItems = computed(() => (selectedGroup.value?.items || []).filter(item => {
    if (["procurement_target_list", "star_clauses_summary", "qualification_requirements"].includes(item.key)) return !!item.value;
    if (!item.template && !item.custom) return false;
    const status = statusOf(item), mode = edits[item.key]?.mode;
    if (filter.value === "missing") return status === "missing";
    if (filter.value === "low") return ["low", "conflict", "invalid"].includes(status);
    if (filter.value === "todo") return status !== "ok" || !mode;
    return true;
  }));
  const scopeSummary = computed(() => !scopeComplete.value ? "先选好三项类型，再开始填写对应字段。" : "当前：" + axes.map(axis => (scopeAxes.value[axis.key] || []).find(option => option.code === scopeState[axis.key])?.label || scopeState[axis.key]).join(" · ") + "　类型专属字段 " + (scopeData.value?.extra_fields || []).length + " 项 · 不适用 " + Object.keys(scopeData.value?.na || {}).length + " 项");
  const scopeNotice = computed(() => [...(scopeData.value?.conflicts || []), ...(scopeData.value?.template_hint?.note ? [scopeData.value.template_hint.note] : [])].join("  "));
  const placeholderOptions = computed(() => {
    const used = new Set(customFields.value.filter(field => field.key !== customForm.key).map(field => field.placeholder));
    const options = unmapped.value.filter(name => !used.has(name));
    const current = customFields.value.find(field => field.key === customForm.key)?.placeholder;
    return current && !options.includes(current) ? [current, ...options] : options;
  });
  function notify(text, kind = "") { Object.assign(notice, { text, kind }); }
  function toast(text) { toastText.value = text; clearTimeout(toastTimer); toastTimer = setTimeout(() => { toastText.value = ""; }, 3200); }
  function selectGroup(key) { activeGroup.value = key; window.scrollTo({ top: 0, behavior: "smooth" }); }

  /** 从完整状态收集，未渲染分组也参与；显式空串必须提交以覆盖原始抽取值。 */
  function collectValues() {
    return Object.fromEntries(allItems.value.filter(item => item.template).map(item => [String(item.template).trim(), item.na ? "" : valueOf(item).trim()]));
  }
  const payload = computed(() => ({
    template_id: templateId.value, values: collectValues(), missing_mode: missingMode.value,
    custom_fields: allCustom.value.map(field => ({
      key: field.key, label: field.label,
      value: valueOf({ key: field.key, value: field.value }), target: field.target, placeholder: field.placeholder
    }))
  }));
  function persistCustom() {
    const fields = customFields.value.map(field => ({ ...field, value: valueOf({ key: field.key, value: field.value }) }));
    if (!writeLocal(storeKey("customFields"), fields)) toast("浏览器无法保存暂存数据，请在关闭页面前生成文件。");
  }
  function setField(item, value, mode = "edited") {
    if (item.na) return;
    edits[item.key] = { value, mode };
    // 采纳后删掉该建议键，避免用户再清空字段时旧模型输出复活。
    if (mode === "suggested") { delete suggestions.value[item.key]; persistSuggestions(); }
    if (item.custom && !item.cf.scope) persistCustom();
  }
  function acceptAll() {
    for (const item of allItems.value) if (!item.custom && !item.na && item.template && item.value != null && String(item.value).trim()) edits[item.key] = { value: String(item.value), mode: "accepted" };
    toast("已采纳全部 AI 抽取值");
  }
  async function suggest() {
    if (suggesting.value || !ready.value) return;
    suggesting.value = true;
    try { const data = await post("/api/jobs/" + encodeURIComponent(jobId) + "/suggest"); suggestions.value = data.suggestions || {}; persistSuggestions(); toast(data.count ? "已起草 " + data.count + " 条建议值，逐条确认后采纳" : data.note || "没有需要起草的字段"); }
    catch (err) { toast("起草失败：" + err.message); } finally { suggesting.value = false; }
  }

  /** 关闭预览或切换类型时同时取消定时器、请求和旧响应的写入资格。 */
  function cancelPreview() {
    clearTimeout(previewTimer); previewSequence += 1; previewController?.abort(); previewLoading.value = false;
  }
  async function refreshPreview() {
    cancelPreview();
    if (!previewOpen.value || !ready.value) return;
    const sequence = previewSequence;
    previewController = new AbortController(); previewLoading.value = true; previewError.value = "";
    try {
      const data = await api("/api/jobs/" + encodeURIComponent(jobId) + "/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload.value), signal: previewController.signal });
      if (sequence === previewSequence) preview.value = data;
    } catch (err) { if (sequence === previewSequence && err.name !== "AbortError") previewError.value = "预览失败：" + err.message; }
    finally { if (sequence === previewSequence) previewLoading.value = false; }
  }
  const previewSections = computed(() => onlyMissing.value ? filterMissingSections(preview.value?.sections) : preview.value?.sections || []);
  watch(previewOpen, open => { writeLocal(storeKey("previewOpen"), open); if (open) refreshPreview(); else cancelPreview(); });
  watch(() => JSON.stringify(payload.value), () => {
    // 所有改值路径都汇到 payload，这里统一按任务暂存，切页回来才不会丢编辑。
    persistEdits();
    // 值一变就使旧响应失效，防抖期间也不能让旧文档覆盖当前输入。
    cancelPreview();
    if (previewOpen.value && ready.value) previewTimer = setTimeout(refreshPreview, 800);
  });
  async function locateField(name) {
    const group = groups.value.find(item => item.items.some(field => field.template === name || field.custom && field.cf.placeholder === name));
    const item = group?.items.find(field => field.template === name || field.custom && field.cf.placeholder === name);
    if (!item) { toast("「" + name + "」没有对应字段，可用「＋ 自定义字段」绑定该占位符"); return; }
    filter.value = "all"; activeGroup.value = group.key; flashKey.value = ""; await nextTick(); flashKey.value = item.key;
    [...document.querySelectorAll(".field[data-key]")].find(element => element.dataset.key === item.key)?.scrollIntoView({ block: "center", behavior: "smooth" });
    clearTimeout(flashTimer); flashTimer = setTimeout(() => { flashKey.value = ""; }, 2400);
  }

  /** 后端重新下发字段元数据，但所有用户编辑仍留在 edits 中。 */
  async function applyScope() {
    const sequence = ++scopeSequence; cancelPreview();
    if (!scopeComplete.value) { baseGroups.value = []; scopeData.value = null; scopeLoading.value = false; return; }
    scopeLoading.value = true;
    try {
      const query = new URLSearchParams({ template_id: templateId.value, ...scopeState });
      const data = await api("/api/jobs/" + encodeURIComponent(jobId) + "/fields?" + query);
      if (sequence !== scopeSequence) return;
      baseGroups.value = data.groups || []; unmapped.value = data.unmapped_placeholders || []; scopeData.value = data.scope || null;
      scopeAxes.value = data.scope?.axes || scopeAxes.value;
      if (!groups.value.some(group => group.key === activeGroup.value)) activeGroup.value = groups.value[0]?.key || "";
    } catch (err) { if (sequence === scopeSequence) { scopeData.value = null; notify("按类型加载字段失败：" + err.message, "error"); } }
    finally { if (sequence === scopeSequence) { scopeLoading.value = false; if (previewOpen.value) refreshPreview(); } }
  }
  function changeScope() { writeLocal(storeKey("scope"), { ...scopeState }); applyScope(); }
  async function pickTemplate(id) {
    if (scopeLoading.value) return;
    templateId.value = id; dialog("dlgTemplate", false); writeLocal(storeKey("templateId"), id);
    await applyScope();
    const names = currentTemplate.value?.placeholders || [];
    for (const field of customFields.value) if (field.target === "placeholder" && !names.includes(field.placeholder)) { field.target = "append"; field.placeholder = ""; }
    persistCustom(); notify(unmapped.value.length ? "该模板有 " + unmapped.value.length + " 个未匹配占位符，可用自定义字段绑定。" : "", unmapped.value.length ? "warn" : "");
  }
  function openCustom(key) {
    const field = customFields.value.find(item => item.key === key);
    Object.assign(customForm, { key: field?.key || "", label: field?.label || "", value: field ? valueOf({ key, value: field.value }) : "", type: field?.type || "text", target: field?.target || "append", placeholder: field?.placeholder || "", options: (field?.options || []).join("\n") });
    dialog("dlgCustom");
  }
  function saveCustom() {
    const label = customForm.label.trim();
    if (!label) { toast("请填写字段名"); return; }
    if (allItems.value.some(item => item.key !== customForm.key && (item.label?.trim() === label || item.template?.trim() === label))) { toast("已有同名字段或占位符，请换一个名称"); return; }
    if (customForm.type === "money" && customForm.value.trim() && !/^[\d,]+(\.\d+)?$/.test(customForm.value.trim())) { toast("金额只能填数字、千分位逗号和小数点"); return; }
    if (customForm.target === "placeholder" && !placeholderOptions.value.includes(customForm.placeholder)) { toast("请选择要写入的占位符"); return; }
    const previous = customFields.value.find(item => item.key === customForm.key);
    const field = {
      key: previous?.key || "cf_" + crypto.randomUUID(), label, value: customForm.value,
      type: customForm.type, target: customForm.target, placeholder: customForm.target === "placeholder" ? customForm.placeholder : "",
      options: [...new Set(customForm.options.split("\n").map(text => text.trim()).filter(Boolean))], created_at: previous?.created_at || new Date().toISOString()
    };
    if (previous) Object.assign(previous, field); else customFields.value.push(field);
    edits[field.key] = { value: field.value, mode: "edited" }; persistCustom(); dialog("dlgCustom", false);
    toast(previous ? "已更新自定义字段" : "已添加自定义字段");
  }
  function askDelete(key) { deleting.value = customFields.value.find(item => item.key === key); dialog("dlgConfirm"); }
  function deleteCustom() {
    if (!deleting.value) return;
    const key = deleting.value.key; customFields.value = customFields.value.filter(item => item.key !== key); delete edits[key];
    if (!groups.value.some(group => group.key === activeGroup.value)) activeGroup.value = groups.value[0]?.key || "";
    persistCustom(); dialog("dlgConfirm", false); deleting.value = null; toast("已删除自定义字段");
  }
  async function generate() {
    if (!ready.value || generating.value) return;
    generating.value = true; notify("正在渲染招标文件，请稍候…");
    try { await download("/api/jobs/" + encodeURIComponent(jobId) + "/generate", (jobInfo.value.filename || "招标文件").replace(/\.[^.]+$/, "") + "-招标文件.docx", payload.value); notify(""); toast("已生成并开始下载招标文件"); }
    catch (err) { notify("生成失败：" + err.message, "error"); } finally { generating.value = false; }
  }
  async function load() {
    // 必须在第一个 await 之前同步恢复：后续赋值触发的 payload watch 会持久化 edits，
    // 放到异步流程末尾会被空 edits 覆盖掉上一次会话的暂存。
    restoreEdits(); restoreSuggestions();
    previewOpen.value = readLocal(storeKey("previewOpen"), false) === true;
    try {
      if (!jobId) throw new Error("缺少任务参数。请从需求详情页进入填写工作台。");
      jobInfo.value = await api("/api/jobs/" + encodeURIComponent(jobId));
      if (jobInfo.value.status !== "done") throw new Error("该任务尚未完成或不可填写，请返回解析页查看状态。");
      templates.value = (await api("/api/templates")).templates || [];
      // 兼容迁移前保存的普通字符串；新写入值统一采用 JSON。
      let saved = readLocal(storeKey("templateId"), "");
      if (!saved) { try { saved = localStorage.getItem(storeKey("templateId")) || ""; } catch { /* 存储不可用时使用默认模板。 */ } }
      templateId.value = templates.value.find(item => item.id === saved)?.id || templates.value.find(item => item.is_default)?.id || templates.value[0]?.id || "";
      const data = await api("/api/jobs/" + encodeURIComponent(jobId) + "/fields?template_id=" + encodeURIComponent(templateId.value));
      scopeAxes.value = data.scope?.axes || {};
      const scope = readLocal(storeKey("scope"), {}), suggested = data.scope?.suggested || {};
      for (const axis of axes) scopeState[axis.key] = typeof scope?.[axis.key] === "string" ? scope[axis.key] : suggested[axis.key] || "";
      const stored = readLocal(storeKey("customFields"), []);
      customFields.value = Array.isArray(stored) ? stored.filter(item => item && typeof item.key === "string" && item.key.startsWith("cf_") && typeof item.label === "string").map(item => ({ ...item, value: String(item.value ?? ""), type: item.type || "text", target: item.target === "placeholder" ? "placeholder" : "append", placeholder: String(item.placeholder || ""), options: Array.isArray(item.options) ? item.options.map(String) : [] })) : [];
      loading.value = false; await applyScope();
    } catch (err) { notify(err.message, "error"); } finally { loading.value = false; }
  }
  onMounted(load);
  function cleanup() { scopeSequence += 1; cancelPreview(); clearTimeout(toastTimer); clearTimeout(flashTimer); }
  onBeforeUnmount(cleanup); window.addEventListener("pagehide", cleanup, { once: true });
  return { jobInfo, templates, templateId, customFields, edits, suggestions, activeGroup, filter, loading, scopeLoading, generating, suggesting, scopeState, scopeAxes, scopeData, unmapped, notice, toastText, deleting, flashKey, previewOpen, preview, previewLoading, previewError, onlyMissing, missingMode, customForm, axes, filters, scopeComplete, ready, currentTemplate, groups, selectedGroup, previousGroup, nextGroup, targetList, valueOf, groupStats, progress, visibleItems, scopeSummary, scopeNotice, placeholderOptions, selectGroup, setField, acceptAll, suggest, refreshPreview, previewSections, locateField, changeScope, pickTemplate, openCustom, saveCustom, askDelete, deleteCustom, generate, dialog };

}
