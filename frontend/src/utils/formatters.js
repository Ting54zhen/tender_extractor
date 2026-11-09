/* 审核报告与图谱沿用已有展示格式；先转义全部外部文本，再生成受限 HTML/SVG。
 * 页面只可把这里返回的内容交给 v-html，接口原文直接使用 Vue 文本插值。
 */
function esc(text) {
  return String(text == null ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderGraphSvg(g) {
  var COLUMN_ORDER = [
    ["Project", "项目"],
    ["ProcurementItem", "采购标的"],
    ["Qualification", "资格要求"],
    ["ScoringCriterion", "评分点"],
    ["DisqualificationClause", "废标条款"],
  ];
  var COLORS = {
    Project: "#2563eb", ProcurementItem: "#059669", Qualification: "#b45309",
    ScoringCriterion: "#7c3aed", DisqualificationClause: "#dc2626",
  };
  var columns = {};
  var order = [];
  (g.nodes || []).forEach(function (n) {
    if (!columns[n.type]) { columns[n.type] = []; order.push(n.type); }
    columns[n.type].push(n);
  });
  order.sort(function (a, b) {
    var ia = COLUMN_ORDER.findIndex(function (c) { return c[0] === a; });
    var ib = COLUMN_ORDER.findIndex(function (c) { return c[0] === b; });
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  var pos = {};
  var COL_W = 240, NODE_H = 44, GAP = 14, MAX_PER_COL = 8;
  var width = order.length * COL_W + 40;
  var maxRows = 1;
  order.forEach(function (type, ci) {
    var list = columns[type].slice(0, MAX_PER_COL);
    maxRows = Math.max(maxRows, list.length);
    list.forEach(function (n, ri) {
      pos[n.id] = { x: 20 + ci * COL_W, y: 40 + ri * (NODE_H + GAP), type: type, label: n.label };
    });
  });
  var height = 60 + maxRows * (NODE_H + GAP);
  function esc2(t) {
    return String(t == null ? "" : t).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  var svg = '<svg width="' + width + '" height="' + height + '" style="font:12px sans-serif">';
  (g.edges || []).forEach(function (e) {
    var s = pos[e.source], t = pos[e.target];
    if (!s || !t) return;
    var x1 = s.x + 190, y1 = s.y + NODE_H / 2, x2 = t.x, y2 = t.y + NODE_H / 2;
    svg += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + y2 +
      '" stroke="#cbd5e1" stroke-width="1.2"><title>' + esc2(e.predicate) + "</title></line>";
  });
  order.forEach(function (type, ci) {
    var list = columns[type].slice(0, MAX_PER_COL);
    var head = (COLUMN_ORDER.find(function (c) { return c[0] === type; }) || [type, type])[1];
    svg += '<text x="' + (20 + ci * COL_W) + '" y="24" fill="#6b7280" font-weight="600">' +
      esc2(head) + "（" + columns[type].length + "）</text>";
    list.forEach(function (n) {
      var p = pos[n.id];
      var color = COLORS[type] || "#6b7280";
      svg += '<rect x="' + p.x + '" y="' + p.y + '" width="190" height="' + NODE_H +
        '" rx="9" fill="#fff" stroke="' + color + '" stroke-width="1.4">' +
        "<title>" + esc2(n.label) + "</title></rect>";
      var label = n.label.length > 16 ? n.label.slice(0, 15) + "…" : n.label;
      svg += '<text x="' + (p.x + 10) + '" y="' + (p.y + 20) + '" fill="#111827">' + esc2(label) + "</text>";
      if (n.properties && n.properties.value) {
        var v = String(n.properties.value).slice(0, 12);
        svg += '<text x="' + (p.x + 10) + '" y="' + (p.y + 35) + '" fill="#9ca3af">' + esc2(v) + "</text>";
      }
    });
    if (columns[type].length > MAX_PER_COL) {
      svg += '<text x="' + (20 + ci * COL_W) + '" y="' + (40 + MAX_PER_COL * (NODE_H + GAP)) +
        '" fill="#9ca3af">… 另有 ' + (columns[type].length - MAX_PER_COL) + " 个</text>";
    }
  });
  svg += "</svg>";
  return svg;
}


function inline(text) {
  return esc(text)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function mdToHtml(md) {
  var lines = String(md || "").replace(/\r\n/g, "\n").split("\n");
  var out = [], list = null, table = [];

  function closeList() { if (list) { out.push("</" + list + ">"); list = null; } }
  function flushTable() {
    if (!table.length) return;
    var rows = table.map(function (row) {
      return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) { return c.trim(); });
    });
    var header = rows[0] || [];
    out.push("<table><thead><tr>" + header.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>");
    for (var i = 2; i < rows.length; i++) {
      out.push("<tr>" + rows[i].map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>");
    }
    out.push("</tbody></table>");
    table = [];
  }

  lines.forEach(function (raw) {
    var line = raw.replace(/\s+$/, "");
    if (/^\s*\|/.test(line)) { closeList(); table.push(line); return; }
    flushTable();
    if (!line) { closeList(); return; }

    var heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      var level = Math.min(3, heading[1].length);
      out.push("<h" + level + ">" + inline(heading[2]) + "</h" + level + ">");
      return;
    }
    if (/^(---+|\*\*\*+)$/.test(line.trim())) { closeList(); out.push("<hr>"); return; }
    if (/^>\s?/.test(line)) { closeList(); out.push("<blockquote>" + inline(line.replace(/^>\s?/, "")) + "</blockquote>"); return; }

    var bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (bullet) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push("<li>" + inline(bullet[1]) + "</li>");
      return;
    }
    var numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (numbered) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push("<li>" + inline(numbered[1]) + "</li>");
      return;
    }
    closeList();
    out.push("<p>" + inline(line) + "</p>");
  });
  flushTable(); closeList();
  return out.join("\n");
}


export const formatters = { markdown: mdToHtml, graph: renderGraphSvg };
