<script>
import { h } from 'vue';

/**
 * 将结构化 blocks 渲染为预览；文档和模型文本只进入文本节点，避免被当成模板执行。
 * 保留后端的合并单元格及跳过标记，点击占位符只通知上层定位，不修改业务值。
 */
export default {
  props: { sections: { type: Array, default: () => [] } },
  emits: ['locate'],
  setup(props, { emit }) {
    function segments(items) {
      return (items || []).map((segment, index) => {
        if (!['value', 'missing'].includes(segment.kind)) return String(segment.text || '');
        return h('span', {
          key: index, class: 'seg-' + segment.kind,
          'data-pname': segment.name || '', title: '点击定位到该字段',
          onClick: () => emit('locate', segment.name || ''),
        }, String(segment.text || '\u00a0'));
      });
    }

    // 样式仅接受约定的文档级别，表格跨度至少为 1；所有原文均交由 Vue 转义。
    function block(item, index) {
      if (item.type === 'table') return h('table', { key: index, class: 'pv-table' }, [
        h('tbody', {}, (item.rows || []).map((row, ri) => h('tr', { key: ri },
          row.map((cell, ci) => cell.skip ? null : h('td', {
            key: ci, colspan: Math.max(1, Number(cell.colspan) || 1),
            rowspan: Math.max(1, Number(cell.rowspan) || 1),
          }, segments(cell.segments))),
        ))),
      ]);
      const style = ['title', 'h1', 'h2', 'h3', 'body'].includes(item.style) ? item.style : 'body';
      const content = segments(item.segments);
      return h('p', { key: index, class: 'pv-' + style }, content.length ? content : '\u00a0');
    }

    return () => props.sections.map((section, index) => h('section', {
      key: index, class: 'pv-section',
    }, (section.blocks || []).map(block)));
  },
};
</script>
