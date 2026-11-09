<script>
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import DocumentPreview from './DocumentPreview.vue';
import { readLocal, writeLocal } from '../../utils/storage.js';

// 预览面板只展示构建结果并向上通知操作，不请求接口、不持有字段真值。
// 侧栏只有 360–460px，A4 纸固定 210mm 会被横向裁切：外层占位容器按算出的
// 比例撑出真实尺寸，纸张本身用 transform 缩放，点片段跳回字段的 DOM 不受影响。
const jobId = new URLSearchParams(location.search).get("job") || "";
const fitKey = "tender.previewFit." + jobId;

export default {
  components: { DocumentPreview },
  props: ['previewOpen', 'preview', 'previewLoading', 'previewError', 'previewSections', 'onlyMissing', 'generating', 'ready'],
  emits: ['close', 'locate', 'update:onlyMissing', 'refresh', 'generate'],
  setup(props) {
    // 兼容迁移前写入的 "1"/"0" 字符串；默认按预览栏宽度适应。
    const storedFit = readLocal(fitKey, null);
    const fitMode = ref(storedFit === null || (storedFit !== false && storedFit !== 0 && storedFit !== "0"));
    const scrollEl = ref(null), fitEl = ref(null), paperEl = ref(null);
    let observer = null;

    function applyScale() {
      const scroll = scrollEl.value, fit = fitEl.value, paper = paperEl.value;
      if (!scroll || !fit || !paper || !props.previewOpen) return;
      const natural = paper.offsetWidth;
      if (!natural) return;
      const avail = scroll.clientWidth - 32; // .preview-scroll 左右各 16px
      let scale = fitMode.value ? Math.min(1, avail / natural) : 1;
      if (!Number.isFinite(scale) || scale <= 0) scale = 1;
      paper.style.transform = "scale(" + scale + ")";
      fit.style.width = Math.ceil(natural * scale) + "px";
      fit.style.height = Math.ceil(paper.offsetHeight * scale) + "px";
    }
    function setFit(on) {
      fitMode.value = on;
      writeLocal(fitKey, on);
      applyScale();
    }
    // 内容、只看缺失、面板开合都会改变纸张尺寸，DOM 更新后重算比例。
    watch([() => props.preview, () => props.previewSections, () => props.previewOpen, () => props.onlyMissing],
      () => nextTick(applyScale));
    onMounted(() => {
      applyScale();
      if (typeof ResizeObserver !== "undefined" && scrollEl.value) {
        observer = new ResizeObserver(applyScale);
        observer.observe(scrollEl.value);
      }
    });
    onBeforeUnmount(() => observer?.disconnect());
    return { fitMode, scrollEl, fitEl, paperEl, setFit };
  },
};
</script>

<template>
  <aside class="preview-pane" id="previewPane" :hidden="!previewOpen">
    <div class="pv-pane-head">
      <h3>生成前预览</h3>
      <div class="spacer"></div><span class="pv-stats" id="pvStats">{{ preview ? '已填 ' + preview.stats.filled + ' / ' +
        preview.stats.total_placeholders + ' · 待补充 ' + preview.stats.missing : '—' }}</span>
      <button class="btn small ghost" id="btnFitPreview" :class="{ primary: fitMode }" title="按预览栏宽度缩放整页"
        @click="setFit(true)">适应宽度</button>
      <button class="btn small ghost" id="btnFullPreview" :class="{ primary: !fitMode }"
        title="按 A4 原尺寸查看，栏内横向滚动" @click="setFit(false)">100%</button>
      <button class="btn small ghost" id="btnClosePreview" @click="$emit('close')">关闭</button>
    </div>
    <div class="pv-pane-body">
      <div class="pv-loading" :class="{ hidden: !previewLoading }" id="pvLoading">正在生成预览…</div>
      <div class="preview-scroll" id="pvScroll" ref="scrollEl">
        <div class="paper-fit" id="paperFit" ref="fitEl">
          <div class="paper" id="paper" ref="paperEl">
            <p class="pv-body" v-if="previewError">{{ previewError }}</p>
            <DocumentPreview v-else :sections="previewSections" @locate="$emit('locate', $event)"></DocumentPreview>
          </div>
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
