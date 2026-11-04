/* ============================================================================
   shell.js · 全站左侧栏（零依赖）

   为什么用脚本注入而不是每个页面复制一份：
   6 个页面共用同一套导航与品牌区，复制会立刻产生漂移；而本项目的约束是
   不引入构建工具，所以用一段浏览器端小脚本统一渲染，各页面只需
   <div class="shell"><main class="main">…</main></div> 骨架。
   导航是原生 <a>，脚本失败时页面内容仍完整可读。
   ========================================================================== */
(function () {
  "use strict";

  var ICONS = {
    parse: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13.5h6M9 17h4"/>',
    fill: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
    compare: '<rect x="3" y="4" width="7.5" height="16" rx="1.6"/><rect x="13.5" y="4" width="7.5" height="16" rx="1.6"/>',
    projects: '<rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/>',
    company: '<path d="M3 21h18"/><path d="M5 21V6.5L12 3l7 3.5V21"/><path d="M9.5 21v-6h5v6"/>',
    settings: '<path d="M4 7h16M4 12h16M4 17h16"/><circle cx="9" cy="7" r="2.2"/><circle cx="15" cy="12" r="2.2"/><circle cx="9" cy="17" r="2.2"/>',
    shield: '<path d="M12 3l7 3v5.5c0 4.2-2.9 7.6-7 9.5-4.1-1.9-7-5.3-7-9.5V6z"/>'
  };

  var ITEMS = [
    { href: "/", label: "需求解析", icon: "parse" },
    { href: "/fill", label: "填写工作台", icon: "fill" },
    { href: "/compare", label: "文档比对", icon: "compare" },
    { href: "/projects", label: "项目工作台", icon: "projects" },
    { href: "/company", label: "企业档案", icon: "company" },
    { href: "/settings", label: "设置", icon: "settings" }
  ];

  function svg(name) {
    return '<svg viewBox="0 0 24 24" aria-hidden="true">' + (ICONS[name] || "") + "</svg>";
  }

  function currentPath() {
    var p = window.location.pathname.replace(/\.html$/, "").replace(/\/+$/, "");
    return p || "/";
  }

  function isActive(href, path) {
    if (href === "/") return path === "/" || path === "/index";
    return path === href;
  }

  var path = currentPath();
  var links = ITEMS.map(function (item) {
    var cls = "sb-link" + (isActive(item.href, path) ? " active" : "");
    return '<a class="' + cls + '" href="' + item.href + '">' + svg(item.icon) +
      "<span>" + item.label + "</span></a>";
  }).join("");

  var html =
    '<aside class="sidebar">' +
      '<div class="sb-brand">' +
        '<a class="sb-logo" href="/" aria-label="招标需求解析">T</a>' +
        '<span class="sb-word">招标需求解析</span>' +
      "</div>" +
      '<a class="sb-new" href="/">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>' +
        "<span>新建解析</span>" +
      "</a>" +
      '<nav class="sb-nav">' + links + "</nav>" +
      '<div class="sb-foot">' +
        '<a class="sb-link" href="/settings">' + svg("shield") + "<span>隐私与模型</span></a>" +
        '<div class="sb-local"><i></i>本机处理 · 数据不出机</div>' +
      "</div>" +
    "</aside>";

  var shell = document.querySelector(".shell");
  if (shell) shell.insertAdjacentHTML("afterbegin", html);
})();
