/* ============================================================================
   preview.js · 把后端的结构化 blocks 渲染成 HTML（填写工作台 / 模板库共用）

   为什么抽成共享文件：两处都要显示同一套模板，纸张样式与片段规则一旦
   各写一份就会漂移；而项目不引构建工具，用一段全局小脚本最省事。

   所有来自后端的内容一律走 esc()，不允许把接口文本直接拼进 innerHTML。
   带 name 的片段输出 data-pname，页面自行绑定「点击定位字段」。
   ========================================================================== */
(function () {
  "use strict";

  function esc(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function segmentsHtml(segments) {
    if (!segments || !segments.length) return "";
    return segments.map(function (seg) {
      var text = esc(seg.text || "");
      if (seg.kind === "value") {
        return '<span class="seg-value" data-pname="' + esc(seg.name || "") +
          '" title="点击定位到该字段">' + text + "</span>";
      }
      if (seg.kind === "missing") {
        return '<span class="seg-missing" data-pname="' + esc(seg.name || "") +
          '" title="点击定位到该字段">' + (text || "&nbsp;") + "</span>";
      }
      return text;
    }).join("");
  }

  function cellsHtml(cells) {
    return (cells || []).map(function (cell) {
      if (cell.skip) return ""; // 被合并覆盖的位置不渲染，避免表格错位
      var attrs = "";
      var colspan = Number(cell.colspan || 1);
      var rowspan = Number(cell.rowspan || 1);
      if (colspan > 1) attrs += ' colspan="' + colspan + '"';
      if (rowspan > 1) attrs += ' rowspan="' + rowspan + '"';
      return "<td" + attrs + ">" + segmentsHtml(cell.segments) + "</td>";
    }).join("");
  }

  function blockHtml(block) {
    if (!block) return "";
    if (block.type === "table") {
      var rows = (block.rows || []).map(function (row) {
        return "<tr>" + cellsHtml(row) + "</tr>";
      }).join("");
      return '<table class="pv-table">' + rows + "</table>";
    }
    var allowed = { title: 1, h1: 1, h2: 1, h3: 1, body: 1 };
    var style = allowed[block.style] ? block.style : "body";
    var content = segmentsHtml(block.segments);
    // 空段落在 Word 里有版面意义，保留成一行
    return '<p class="pv-' + style + '">' + (content || "&nbsp;") + "</p>";
  }

  function render(sections) {
    return (sections || []).map(function (section) {
      var blocks = (section.blocks || []).map(blockHtml).join("");
      // section.title 只用于目录；章节标题本身已在 blocks 里，不重复渲染
      return '<section class="pv-section">' + blocks + "</section>";
    }).join("");
  }

  function hasMissing(block) {
    if (!block) return false;
    if (block.type !== "table") {
      return (block.segments || []).some(function (s) { return s.kind === "missing"; });
    }
    return (block.rows || []).some(function (row) {
      return (row || []).some(function (cell) {
        return !cell.skip && (cell.segments || []).some(function (s) {
          return s.kind === "missing";
        });
      });
    });
  }

  function onlyMissing(sections) {
    return (sections || [])
      .map(function (section) {
        return {
          title: section.title,
          blocks: (section.blocks || []).filter(hasMissing)
        };
      })
      .filter(function (section) { return section.blocks.length; });
  }

  window.TenderPreview = {
    esc: esc,
    render: render,
    onlyMissing: onlyMissing
  };
})();
