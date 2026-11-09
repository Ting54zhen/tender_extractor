// 原生 dialog 负责焦点与模态行为，内容由 Vue 响应式状态驱动。
/** 原生 dialog 只负责焦点和模态行为，内容与业务状态由 Vue 管理。 */
export function dialog(id, open = true) {
  const element = document.getElementById(id);
  if (!element) return;
  if (open && !element.open) element.showModal();
  if (!open && element.open) element.close();
}
