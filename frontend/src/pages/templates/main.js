import { createApp } from 'vue';
import Page from './TemplatesPage.vue';
import '../../styles/app.css';
import '../../styles/base.css';
import '../../styles/preview.css';
import '../../styles/pages/templates.css';

// 七页独立挂载、普通链接跳转，查询参数与原后端路径保持一致。
createApp(Page).mount('#vue-root');
