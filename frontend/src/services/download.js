// 下载仅处理后端返回的二进制，不改变文档内容与文件名约定。
/** 按服务端建议的文件名下载；延后回收 URL，避免浏览器尚未开始读就失效。 */
export function saveBlob(blob, name) {
  const link = document.createElement("a");
  const url = URL.createObjectURL(blob);
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

export async function download(path, fallbackName, data) {
  const options = data === undefined ? { method: "POST" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
  };
  const response = await fetch(path, options);
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.error || ("HTTP " + response.status));
  }
  const match = /filename\*=UTF-8''([^;]+)/.exec(response.headers.get("Content-Disposition") || "");
  saveBlob(await response.blob(), match ? decodeURIComponent(match[1]) : fallbackName);
}
