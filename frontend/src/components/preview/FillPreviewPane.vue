<script>
import DocumentPreview from './DocumentPreview.vue';
import PreviewDialog from './PreviewDialog.vue';
// 弹窗只展示构建结果并通知操作，保持原有请求防抖、旧响应丢弃和生成契约。
export default {
  components: { DocumentPreview, PreviewDialog },
  props: ['previewOpen', 'preview', 'previewLoading', 'previewError', 'previewSections', 'onlyMissing', 'generating', 'ready'],
  emits: ['close', 'locate', 'update:onlyMissing', 'refresh', 'generate'],
};
</script>

<template>
  <PreviewDialog id="previewPane" title="生成前预览" :open="previewOpen" @close="$emit('close')">
    <template #toolbar><div class="preview-dialog-tools">
      <span class="tag t-ok" id="pvStats">已填 {{ preview?.stats.filled ?? 0 }} / {{ preview?.stats.total_placeholders ?? 0 }}</span>
      <span class="tag t-low">待补充 {{ preview?.stats.missing ?? 0 }}</span>
      <label class="chip"><input type="checkbox" id="pvOnlyMissing" :checked="onlyMissing" @change="$emit('update:onlyMissing', $event.target.checked)">只看缺失</label>
      <div class="spacer"></div><button class="btn small" id="btnRefreshPreview" @click="$emit('refresh')">刷新</button>
    </div></template>
    <div class="preview-content"><div class="pv-loading" :class="{ hidden: !previewLoading }" id="pvLoading" role="status">正在生成预览…</div>
      <div class="preview-scroll"><div class="paper" id="paper">
        <p class="pv-body" v-if="previewError">{{ previewError }}</p>
        <DocumentPreview v-else :sections="previewSections" @locate="$emit('locate', $event)" />
      </div></div>
    </div>
    <template #hint>预览为结构近似，字体与分页以生成的 .docx 为准；点黄色「待补充」或已填值可关闭预览并定位字段。</template>
    <template #footer><button class="btn" id="btnClosePreview" @click="$emit('close')">继续填写</button>
      <button class="btn primary" id="btnGenerate2" :disabled="generating || !ready" @click="$emit('generate')">生成并下载</button></template>
  </PreviewDialog>
</template>
