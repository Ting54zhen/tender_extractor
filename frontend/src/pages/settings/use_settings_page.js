import { ref, onMounted, onBeforeUnmount } from 'vue';
import { api, post } from '../../services/api.js';
import { formDefinitions } from '../../config/form_definitions.js';

/** SettingsPage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  const settings = ref({}), overrides = ref({}), error = ref(""), busy = ref(false), saved = ref(false), loaded = ref(false);
  const definitions = formDefinitions.settings;
  const sections = [{ key: "llm", title: "模型接入（抽取）" }, { key: "review", title: "第三方复核" }, { key: "extraction", title: "抽取与校验" }, { key: "output", title: "输出" }];
  let savedTimer = null;
  function apply(data) {
    settings.value = data.settings || {};
    for (const section of sections) settings.value[section.key] ||= {};
    overrides.value = data.overrides || {}; loaded.value = true;
  }
  async function load() { try { apply(await api("/api/settings")); } catch (err) { error.value = err.message; } }
  function inputId(section, key) { return "f-" + section + "-" + key; }
  function isOverridden(section, key) { return Object.prototype.hasOwnProperty.call(overrides.value[section] || {}, key); }
  function setValue(section, field, value) { settings.value[section][field.key] = field.type === "number" ? value === "" ? null : Number(value) : value; }
  async function persist(payload) {
    if (busy.value) return;
    busy.value = true; error.value = ""; saved.value = false;
    try {
      apply(await post("/api/settings", payload));
      saved.value = true; clearTimeout(savedTimer); savedTimer = setTimeout(() => { saved.value = false; }, 2500);
    } catch (err) { error.value = err.message; }
    finally { busy.value = false; }
  }
  function save() {
    // 只提交既有可编辑项，不把响应中的密钥等其他配置重新回写。
    const values = {};
    for (const section of sections) values[section.key] = Object.fromEntries(definitions[section.key].map(field => [field.key, settings.value[section.key][field.key] ?? null]));
    return persist({ settings: values });
  }
  async function reset() { if (confirm("清除全部覆盖值，恢复出厂默认设置？")) await persist({ reset: true }); }
  onMounted(load); onBeforeUnmount(() => clearTimeout(savedTimer));
  return { settings, overrides, error, busy, saved, loaded, definitions, sections, inputId, isOverridden, setValue, save, reset };

}
