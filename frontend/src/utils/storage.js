// 浏览器存储按任务键隔离；禁用存储时由页面展示暂存失败提示。
/** 浏览器禁用存储时仍允许本次操作；持久化失败通过返回值交给页面提示。 */
export function readLocal(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key) || "null") ?? fallback; }
  catch { return fallback; }
}
export function writeLocal(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); return true; }
  catch { return false; }
}
