/**
 * 仅整理详情显示：采购条款常把多个编号挤在同一段，按明确的条目符号换行。
 * 不改接口原值和证据，只返回展示文本；小数、日期、网址及代码片段不得拆开。
 */
export function numberedText(value) {
  return String(value ?? '').split(/(`[^`]*`|https?:\/\/[^\s<>]+)/g).map((part, index) => {
    if (index % 2) return part;
    return part.replace(/(?<![\dA-Za-z_./:（(])(?:[（(]\d{1,3}[）)]|\d{1,3}[、．)]|\d{1,3}\.(?!\d))(?=[ \t]*[^\s\d])/g,
      (marker, offset, source) => offset && !/\n[ \t]*$/.test(source.slice(0, offset)) ? '\n' + marker : marker);
  }).join('');
}
