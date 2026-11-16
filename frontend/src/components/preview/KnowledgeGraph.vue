<script>
import { ref, watch } from 'vue';
import PreviewDialog from './PreviewDialog.vue';
import { formatters } from '../../utils/formatters.js';

// 默认图谱等比适配，无滚动条；大图在统一预览弹窗内缩放，滚动只用于放大后的查看。
export default {
  components: { PreviewDialog }, props: { graph: { type: Object, default: () => ({}) } },
  setup(props) {
    const open = ref(false), zoom = ref(100);
    watch(open, value => { if (value) zoom.value = 100; });
    function scale(delta) { zoom.value = Math.max(50, Math.min(200, zoom.value + delta)); }
    return { open, zoom, scale, formatters };
  },
};
</script>

<template>
  <div class="knowledge-graph">
    <div class="graph-toolbar"><span class="hintline">知识图谱</span><div class="spacer"></div><button class="btn small" id="graph-expand" @click="open = true">放大查看</button></div>
    <div id="graph-svg" class="graph-fit" v-html="formatters.graph(graph)"></div>
    <PreviewDialog id="graphPreview" title="知识图谱预览" :open="open" @close="open = false">
      <template #toolbar><div class="preview-dialog-tools"><button class="btn small" data-graph-out :disabled="zoom <= 50" @click="scale(-25)">缩小</button>
        <span class="hintline">{{ zoom }}%</span><button class="btn small" data-graph-in :disabled="zoom >= 200" @click="scale(25)">放大</button>
        <button class="btn small" @click="zoom = 100">适应窗口</button></div></template>
      <div class="graph-scroll"><div class="graph-fit" :style="{ width: zoom + '%' }" v-html="formatters.graph(graph)"></div></div>
      <template #hint>默认显示完整图谱；放大后可滚动查看，节点完整文字可通过悬停查看。</template>
      <template #footer><button class="btn" @click="open = false">关闭</button></template>
    </PreviewDialog>
  </div>
</template>
