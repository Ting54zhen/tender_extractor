import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath } from 'node:url';

// HTML 是真实的七个入口，交给 Python 保留已有路由，不引入客户端 Router。
const pages = ['index', 'fill', 'templates', 'projects', 'company', 'settings', 'compare'];
const root = fileURLToPath(new URL('.', import.meta.url));

export default defineConfig({
  root,
  appType: 'mpa',
  plugins: [vue()],
  // 开发服务和预览都仅监听回环地址；模型和文档请求仍由本机 Python 处理。
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: { '/api': { target: 'http://127.0.0.1:8765' } },
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
  build: {
    outDir: 'dist', emptyOutDir: true, sourcemap: false,
    // 多页面共享依赖由构建器去重，生产浏览器无需 npm 或模板编译器。
    rolldownOptions: { input: Object.fromEntries(pages.map(page => [page, fileURLToPath(new URL(`${page}.html`, import.meta.url))])) },
  },
});
