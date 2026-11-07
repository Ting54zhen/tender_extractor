// 校验生产构建能独立部署：七个入口、递归模块引用和许可证必须在 dist 内闭合。
// 此检查只读本地文件；交互与 API 契约交由真实浏览器和 Python 测试验证。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const dist = path.join(root, 'dist');
const pages = ['index', 'fill', 'templates', 'projects', 'company', 'settings', 'compare'];
const visited = new Set();

/** 将构建器生成的绝对资源 URL 或相对模块导入解析为 dist 内的真实文件。 */
function inspect(reference, parent = path.join(dist, 'index.html')) {
  if (reference.startsWith('data:')) return;
  assert.ok(!/^(?:[a-z]+:|\/\/)/i.test(reference), '产物不能引用外部资源：' + reference);
  const target = reference.startsWith('/')
    ? path.resolve(dist, '.' + reference)
    : path.resolve(path.dirname(parent), reference);
  assert.ok(target.startsWith(dist + path.sep), '资源越出构建目录：' + reference);
  assert.ok(fs.statSync(target).isFile(), '缺少资源：' + target);
  if (visited.has(target)) return;
  visited.add(target);
  const text = fs.readFileSync(target, 'utf8');
  assert.ok(text.length, '空资源：' + target);
  if (target.endsWith('.js')) {
    // 包含静态 import、export from 与动态 import；只匹配语句中的模块引用。
    const imports = /(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)["'`]([^"'`]+)["'`]/g;
    for (const match of text.matchAll(imports)) inspect(match[1], target);
    assert.ok(!text.includes('/src/pages/'), '构建结果仍在引用源文件');
  }
  if (target.endsWith('.css')) {
    for (const match of text.matchAll(/url\(\s*["']?([^\s"')]+)["']?\s*\)/g)) inspect(match[1], target);
    assert.ok(!/@import/i.test(text), 'CSS 应由构建器展开导入');
  }
}

for (const page of pages) {
  const html = fs.readFileSync(path.join(dist, page + '.html'), 'utf8');
  assert.ok(html.includes('id="vue-root"'), page + ' 缺少挂载点');
  assert.match(html, /<script\b[^>]*type="module"/);
  for (const match of html.matchAll(/\b(?:src|href)="([^"]+)"/g)) inspect(match[1]);
}
assert.match(fs.readFileSync(path.join(dist, 'assets/VUE-LICENSE.txt'), 'utf8'), /MIT License/);
assert.match(fs.readFileSync(path.join(dist, 'assets/VITE-LICENSE.txt'), 'utf8'), /MIT License/);
assert.ok(fs.readFileSync(path.join(dist, 'assets/THIRD-PARTY-NOTICES.txt'), 'utf8').length);
assert.ok(!fs.existsSync(path.join(dist, 'src')) && !fs.existsSync(path.join(dist, 'node_modules')));
console.log(`前端构建检查通过：${pages.length} 个页面，${visited.size} 个本地资源；许可证与离线引用完整。`);
