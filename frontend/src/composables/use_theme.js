import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { readLocal, writeLocal } from '../utils/storage.js';

/** 主题只保存在浏览器本机；普通多页面跳转由前置脚本恢复，不修改后端设置。
 * storage 事件同步其他标签页，监听器随导航组件销毁，避免重复注册。
 */
export function useTheme() {
  const dark = ref(document.documentElement.dataset.theme === 'dark');
  const message = ref('');
  const label = computed(() => dark.value ? '切换为浅色模式' : '切换为深色模式');
  function apply(theme) {
    dark.value = theme === 'dark';
    document.documentElement.dataset.theme = dark.value ? 'dark' : 'light';
  }
  function toggleTheme() {
    const theme = dark.value ? 'light' : 'dark';
    apply(theme);
    message.value = writeLocal('tender.theme', theme)
      ? (dark.value ? '已切换为深色模式' : '已切换为浅色模式')
      : '主题已切换，浏览器存储不可用，刷新后将恢复默认主题';
  }
  function syncTheme(event) {
    if (event.key === 'tender.theme' || event.key === null) apply(readLocal('tender.theme', 'light'));
  }
  onMounted(() => window.addEventListener('storage', syncTheme));
  onBeforeUnmount(() => window.removeEventListener('storage', syncTheme));
  return { dark, themeLabel: label, themeMessage: message, toggleTheme };
}
