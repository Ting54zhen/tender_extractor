<script>
import { useTheme } from '../../composables/use_theme.js';
/* 共享导航采用 Vue 组件，普通 a 链接继续走 Python 现有多页面路径。
 * 图标全部为项目内固定 SVG，不把接口或文档文本作为 HTML 注入。
 */
var ICONS = {
  parse: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13.5h6M9 17h4"/>',
  fill: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  compare: '<rect x="3" y="4" width="7.5" height="16" rx="1.6"/><rect x="13.5" y="4" width="7.5" height="16" rx="1.6"/>',
  projects: '<rect x="3" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.6"/><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.6"/><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.6"/>',
  company: '<path d="M3 21h18"/><path d="M5 21V6.5L12 3l7 3.5V21"/><path d="M9.5 21v-6h5v6"/>',
  settings: '<path d="M4 7h16M4 12h16M4 17h16"/><circle cx="9" cy="7" r="2.2"/><circle cx="15" cy="12" r="2.2"/><circle cx="9" cy="17" r="2.2"/>',
  templates: '<rect x="4" y="3" width="12" height="16" rx="1.6"/><path d="M8 19h10a2 2 0 0 0 2-2V7"/><path d="M7.5 8h5M7.5 12h5"/>',
  shield: '<path d="M12 3l7 3v5.5c0 4.2-2.9 7.6-7 9.5-4.1-1.9-7-5.3-7-9.5V6z"/>'
};

var ITEMS = [
  { href: "/", label: "需求解析", icon: "parse" },
  { href: "/fill", label: "填写工作台", icon: "fill" },
  { href: "/templates", label: "模板库", icon: "templates" },
  { href: "/projects", label: "项目工作台", icon: "projects" },
  { href: "/compare", label: "文档比对", icon: "compare" },
  { href: "/company", label: "企业档案", icon: "company" },
  { href: "/settings", label: "设置", icon: "settings" }
];


export default {
  setup() {
    const path = location.pathname.replace(/\.html$/, "").replace(/\/+$/, "") || "/";
    // 新建解析是独立入口，不选中需求记录列表；详情带 job 时仍归属需求解析。
    const query = new URLSearchParams(location.search);
    const isNew = query.get('view') === 'new' && !query.has('job');
    return {
      ...useTheme(),
      items: ITEMS, icons: ICONS, path,
      active: href => href === "/" ? !isNew && (path === "/" || path === "/index") : path === href
    };
  }
};
</script>

<template>
  <aside class="sidebar">
    <div class="sb-brand"><a class="sb-logo" href="/" aria-label="招标需求解析">T</a><span class="sb-word">招标需求解析</span>
      <!-- 主题入口位于品牌旁，窄屏折叠侧栏后仍可直接使用；图标为本地 SVG。 -->
      <button type="button" class="theme-toggle" id="theme-toggle" :aria-label="themeLabel" :title="themeLabel"
          :aria-pressed="dark" @click="toggleTheme"><svg viewBox="0 0 24 24" aria-hidden="true">
          <template v-if="dark"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5"/></template>
          <path v-else d="M20 15.5A8.5 8.5 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z"/>
        </svg></button><span class="sr-only" role="status">{{ themeMessage }}</span>
    </div>
    <a class="sb-new" href="/?view=new"><svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg><span>新建解析</span></a>
    <nav class="sb-nav"><a v-for="item in items" :key="item.href" :href="item.href" class="sb-link"
        :class="{ active: active(item.href) }">
        <svg viewBox="0 0 24 24" aria-hidden="true" v-html="icons[item.icon]"></svg><span>{{ item.label }}</span>
      </a></nav>
    <div class="sb-foot"><a class="sb-link" href="/settings"><svg viewBox="0 0 24 24" aria-hidden="true"
          v-html="icons.shield"></svg><span>隐私与模型</span></a>
      <div class="sb-local"><i></i>本机处理 · 数据不出机</div>
    </div>
  </aside>
</template>
