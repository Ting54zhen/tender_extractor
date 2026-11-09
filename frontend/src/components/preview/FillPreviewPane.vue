<script>
import DocumentPreview from './DocumentPreview.vue';
// 预览面板只展示构建结果并向上通知操作，不请求接口、不持有字段真值。
export default {
  components: { DocumentPreview },
  props: ['previewOpen', 'preview', 'previewLoading', 'previewError', 'previewSections', 'onlyMissing', 'generating', 'ready'],
  emits: ['close', 'locate', 'update:onlyMissing', 'refresh', 'generate'],
};
</script>

<template>
  <aside class="preview-pane" id="previewPane" :hidden="!previewOpen">
    <div class="pv-pane-head">
      <h3>生成前预览</h3>
      <div class="spacer"></div><span class="pv-stats" id="pvStats">{{ preview ? '已填 ' + preview.stats.filled + ' / ' +
        preview.stats.total_placeholders + ' · 待补充 ' + preview.stats.missing : '—' }}</span><button
        class="btn small ghost" id="btnClosePreview" @click="$emit('close')">关闭</button>
    </div>
    <div class="pv-pane-body">
      <div class="pv-loading" :class="{ hidden: !previewLoading }" id="pvLoading">正在生成预览…</div>
      <div class="preview-scroll">
        <div class="paper" id="paper">
          <p class="pv-body" v-if="previewError">{{ previewError }}</p>
          <DocumentPreview v-else :sections="previewSections" @locate="$emit('locate', $event)"></DocumentPreview>
        </div>
      </div>
    </div>
    <div class="pv-pane-foot"><label class="chip"><input type="checkbox" id="pvOnlyMissing" :checked="onlyMissing"
          @change="$emit('update:onlyMissing', $event.target.checked)"> 只看缺失</label>
      <div class="spacer"></div><button class="btn small" id="btnRefreshPreview"
        @click="$emit('refresh')">刷新</button><button class="btn small primary" id="btnGenerate2"
        :disabled="generating || !ready" @click="$emit('generate')">生成并下载</button>
    </div>
    <div class="pv-hint">预览为结构近似，字体与分页以生成的 .docx 为准；点黄色「待补充」或已填值可跳回字段。</div>
  </aside>
</template>
