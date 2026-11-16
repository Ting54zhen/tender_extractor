/* 七个页面在加载样式和 Vue 前恢复本机主题，避免深色页面刷新时先闪出白底。
 * 只接受两个固定值；浏览器禁用存储时继续使用默认浅色，不影响页面操作。 */
(function () {
  var theme = 'light';
  try {
    var saved = JSON.parse(localStorage.getItem('tender.theme') || 'null');
    if (saved === 'dark') theme = 'dark';
  } catch { /* 存储不可用或值损坏时采用默认浅色。 */ }
  document.documentElement.dataset.theme = theme;
}());
