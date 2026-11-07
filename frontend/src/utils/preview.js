// 只看缺失时保留整块表格，避免破坏合并单元格结构。
function hasMissing(block) {
  const items = block.type === "table"
    ? (block.rows || []).flat().filter(cell => !cell.skip).flatMap(cell => cell.segments || [])
    : block.segments || [];
  return items.some(segment => segment.kind === "missing");
}
export function onlyMissing(sections) {
  return (sections || []).map(section => ({ ...section, blocks: (section.blocks || []).filter(hasMissing) }))
    .filter(section => section.blocks.length);
}
