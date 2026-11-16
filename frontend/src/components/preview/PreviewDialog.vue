<script>
import { ref, watch, onMounted } from 'vue';

/** 三处预览共用居中模态外壳；原生 dialog 处理焦点、Escape 和背景不可操作。
 * open 未传入时兼容模板库现有 dialog() 调用，传入时由父页面控制开关。
 * 放大只影响窗口尺寸，不改变原文、图谱坐标或正在填写的字段。
 */
export default {
  props: { id: { type: String, required: true }, title: String, open: { default: undefined } },
  emits: ['close'],
  setup(props, { emit }) {
    const element = ref(null), expanded = ref(false);
    function sync(open) {
      if (open === true && !element.value?.open) element.value?.showModal();
      if (open === false && element.value?.open) element.value?.close();
    }
    function close() { element.value?.close(); }
    function closed() { expanded.value = false; emit('close'); }
    watch(() => props.open, sync); onMounted(() => sync(props.open));
    return { element, expanded, close, closed };
  },
};
</script>

<template>
  <dialog :id="id" ref="element" class="dlg-preview" :class="{ 'preview-expanded': expanded }"
    :aria-labelledby="id + '-title'" @close="closed">
    <div class="dlg-body">
      <div class="preview-dialog-head"><h3 :id="id + '-title'">{{ title }}</h3><div class="spacer"></div>
        <button type="button" class="btn small" data-preview-expand :aria-pressed="expanded" @click="expanded = !expanded">{{ expanded ? '还原窗口' : '放大窗口' }}</button>
        <button type="button" class="btn small ghost" data-preview-close @click="close">关闭</button>
      </div>
      <slot name="toolbar"></slot><slot></slot>
      <p class="pv-hint"><slot name="hint"></slot></p>
      <div class="dlg-foot"><slot name="footer"></slot></div>
    </div>
  </dialog>
</template>
