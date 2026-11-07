// 状态来自原始抽取结果，人工填写不冒充有证据的 AI 抽取。
export function statusOf(item) {
  if (item.value == null || String(item.value).trim() === "") return "missing";
  if (["conflict", "invalid"].includes(item.status)) return item.status;
  return ["low_confidence", "inferred"].includes(item.status) ? "low" : "ok";
}
