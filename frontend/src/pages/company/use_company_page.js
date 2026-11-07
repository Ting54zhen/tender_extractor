import { ref, onMounted, onBeforeUnmount } from 'vue';
import { api, post } from '../../services/api.js';
import { formDefinitions } from '../../config/form_definitions.js';

/** CompanyPage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  const profile = ref(null), evidence = ref([]), error = ref(""), busy = ref(false), saved = ref(false);
  const basic = formDefinitions.basic, lists = formDefinitions.lists;
  const sections = [{ key: "qualifications", title: "资质证书", add: "添加资质" }, { key: "performances", title: "同类项目业绩", add: "添加业绩" }, { key: "personnel", title: "拟投入人员", add: "添加人员" }];
  const rowKeys = new WeakMap(); let nextKey = 0, savedTimer = null;
  // 内部稳定标识不写入业务对象，编辑或删除一行不会复用其他行的输入节点。
  function rowKey(item) { if (!rowKeys.has(item)) rowKeys.set(item, ++nextKey); return rowKeys.get(item); }
  function apply(value) {
    profile.value = value;
    profile.value.company ||= {};
    for (const section of sections) profile.value[section.key] ||= [];
    evidence.value = Object.entries(value.evidence_index || {}).map(([key, text]) => ({ id: ++nextKey, key, value: text }));
  }
  function addRow(section) { profile.value[section].push(Object.fromEntries(lists[section].fields.map(([key]) => [key, ""]))); }
  function addEvidence() { evidence.value.push({ id: ++nextKey, key: "", value: "" }); }
  async function save() {
    if (busy.value || !profile.value) return;
    const keys = evidence.value.map(item => item.key.trim()).filter(Boolean);
    if (new Set(keys).size !== keys.length) { error.value = "材料索引 key 不能重复"; return; }
    const values = {
      ...profile.value, company: Object.fromEntries(basic.map(([key]) => [key, String(profile.value.company[key] || "").trim()])),
      evidence_index: Object.fromEntries(evidence.value.filter(item => item.key.trim()).map(item => [item.key.trim(), item.value.trim()]))
    };
    busy.value = true; error.value = ""; saved.value = false;
    try { apply((await post("/api/company", values)).profile); saved.value = true; clearTimeout(savedTimer); savedTimer = setTimeout(() => { saved.value = false; }, 2500); }
    catch (err) { error.value = err.message; } finally { busy.value = false; }
  }
  onMounted(async () => { try { apply((await api("/api/company")).profile); } catch (err) { error.value = err.message; } });
  onBeforeUnmount(() => clearTimeout(savedTimer));
  return { profile, evidence, error, busy, saved, basic, lists, sections, rowKey, addRow, addEvidence, save };

}
