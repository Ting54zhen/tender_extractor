import { ref, reactive, computed, onMounted } from 'vue';
import { api } from '../../services/api.js';
import { fmtSize } from '../../utils/format_size.js';
import { useJobPolling } from '../../composables/use_job_polling.js';

/** ComparePage 的状态与业务动作；页面模板独立维护，不通过 DOM 反向收集字段值。 */
export function usePage() {
  const files = reactive({ tender: null, bid: null }), dragging = ref(""), useLlm = ref(true), error = ref(""), busy = ref(false), payload = ref(null), job = ref(null), health = ref({});
  const slots = [{ key: "tender", label: "招标文件", hint: "要求什么" }, { key: "bid", label: "投标文件", hint: "能提供什么" }];
  const verdictText = { satisfied: "满足", partial: "部分满足", missing: "未响应" };
  const stats = computed(() => { const data = job.value?.summary || {}; return [["资格要求", data.requirements || 0], ["投标响应", data.responses || 0], ["满足", data.satisfied || 0], ["部分满足", data.partial || 0], ["未响应", data.missing || 0], ["用时", (job.value?.elapsed || 0) + "s"]]; });
  async function loadResult(value) { payload.value = await api("/api/jobs/" + encodeURIComponent(value.id) + "/compare"); job.value = value; }
  const { progress, start } = useJobPolling(loadResult, text => { error.value = text; });
  function choose(key) { document.getElementById("file-" + key).click(); }
  function setFile(key, file) {
    if (!file) return;
    if (!/\.(docx|pdf|txt)$/i.test(file.name)) { error.value = "仅支持 .docx / .pdf / .txt"; return; }
    if (!file.size || file.size > 50 * 1024 * 1024) { error.value = !file.size ? "文件内容为空" : "文件超过 50MB 上限"; return; }
    files[key] = file; error.value = "";
  }
  async function submit() {
    if (!files.tender || !files.bid || busy.value || progress.running) return;
    busy.value = true; error.value = ""; payload.value = null;
    const form = new FormData(); form.append("tender", files.tender, files.tender.name); form.append("bid", files.bid, files.bid.name); form.append("use_llm", useLlm.value ? "1" : "0");
    try { await start(await api("/api/compare", { method: "POST", body: form })); } catch (err) { error.value = err.message; } finally { busy.value = false; }
  }
  async function loadHealth() { try { health.value = await api("/api/health"); } catch (err) { health.value = { available: false, reason: err.message }; } }
  onMounted(async () => {
    loadHealth(); const id = new URLSearchParams(location.search).get("job"); if (!id) return;
    try { const value = await api("/api/jobs/" + encodeURIComponent(id)); if (["queued", "running"].includes(value.status)) await start(value); else if (value.status === "error") error.value = value.error || "比对失败"; else await loadResult(value); }
    catch (err) { error.value = err.message; }
  });
  return { files, dragging, useLlm, error, busy, payload, job, health, slots, verdictText, stats, progress, choose, setFile, submit, loadHealth, fmtSize };

}
