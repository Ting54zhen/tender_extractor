<script setup>
import { ref, watch, onBeforeUnmount } from 'vue';

// 需求与项目复用同一确认样式；原生 dialog 保留焦点约束与 Escape 键语义。
const props = defineProps({ open: Boolean, title: String, message: String, busy: Boolean, error: String });
const emit = defineEmits(['cancel', 'confirm']);
const element = ref(null);
// Chromium 会从 CSS 视口单位与 fixed 包含块中扣除预留滚动条槽。
// 使用窗口实际宽度确定横向中点，保持原有「整窗居中」语义；仅弹窗打开时监听缩放。
function centerInWindow() {
  if (element.value && props.open) element.value.style.left = window.innerWidth / 2 + 'px';
}
watch([() => props.open, element], ([open]) => {
  if (!element.value) return;
  window.removeEventListener('resize', centerInWindow);
  if (open) { centerInWindow(); window.addEventListener('resize', centerInWindow); }
  if (open && !element.value.open) element.value.showModal();
  if (!open && element.value.open) element.value.close();
}, { flush: 'post' });
onBeforeUnmount(() => window.removeEventListener('resize', centerInWindow));
function cancel(event) {
  // 提交期间保持弹窗，失败原因仍在原位置展示，避免重复删除或丢失错误。
  event?.preventDefault();
  if (!props.busy) emit('cancel');
}
</script>

<template>
  <dialog ref="element" class="delete-dialog" aria-labelledby="delete-title" aria-describedby="delete-message" @cancel="cancel">
    <div class="delete-dialog-body">
      <div class="delete-dialog-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 10v7M14 10v7" /></svg></div>
      <h2 id="delete-title">{{ title }}</h2>
      <p id="delete-message">{{ message }}</p>
      <p class="alert error" role="alert" v-if="error">{{ error }}</p>
      <div class="delete-dialog-actions">
        <button class="btn delete-solid" data-delete-confirm :disabled="busy" @click="emit('confirm')">{{ busy ? '删除中…' : '确认删除' }}</button>
        <button class="btn" data-delete-cancel autofocus :disabled="busy" @click="cancel">取消</button>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
/* 明确以完整视口居中，和页面滚动位置、侧栏宽度无关；窄屏保留边距。
   横向位置由上方窗口尺寸计算覆盖，避免 auto 外边距把预留滚动条槽计入偏移。 */
.delete-dialog { position: fixed; inset: 50% auto auto 50%; margin: 0; transform: translate(-50%, -50%); width: min(440px, calc(100vw - 32px)); max-height: calc(100dvh - 48px); overflow-y: auto; border: 1px solid var(--line); }
.delete-dialog-body { padding: 28px; }
.delete-dialog-icon { width: 44px; height: 44px; display: grid; place-items: center; color: var(--red); background: var(--red-soft); border-radius: 12px; margin-bottom: 18px; }
.delete-dialog-icon svg { width: 23px; height: 23px; fill: none; stroke: currentColor; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
h2 { font-size: 20px; margin: 0 0 10px; font-weight: 600; }
p { color: var(--muted); font-size: 14px; line-height: 1.8; margin: 0; overflow-wrap: anywhere; }
.delete-dialog-actions { display: flex; gap: 10px; margin-top: 26px; }
.delete-dialog-actions .btn { flex: 1; }
.alert { margin-top: 16px; }
</style>
