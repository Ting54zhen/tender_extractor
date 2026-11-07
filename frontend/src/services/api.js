// API 始终使用本机同源路径，保留后端错误和现有请求结构。
/** 统一处理 HTTP 错误；原样保留现有 API 的 JSON 和二进制请求方式。 */
export async function api(path, options) {
  if (!path.startsWith("/api/")) throw new Error("接口必须使用本机 /api/ 路径");
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || ("HTTP " + response.status));
  return data;
}

/** JSON 请求只负责序列化，不改变字段名、空串或业务数据类型。 */
export function post(path, data = {}) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
}
