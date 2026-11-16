// 展示排版不得改动数字含义或启用 HTML；用脱敏条款覆盖常见编号和误拆风险。
import assert from 'node:assert/strict';
import { numberedText } from '../frontend/src/utils/detail_text.js';
import { formatters } from '../frontend/src/utils/formatters.js';
assert.equal(numberedText('1、提交资料；2、安装调试；3、验收。'), '1、提交资料；\n2、安装调试；\n3、验收。');
assert.equal(numberedText('要求：（1）资质有效；（2）按期交付。'), '要求：\n（1）资质有效；\n（2）按期交付。');
assert.equal(numberedText('1. 提交报告 2. 组织验收 3) 完成培训'), '1. 提交报告 \n2. 组织验收 \n3) 完成培训');
assert.equal(numberedText('预算123456.78元，版本v1.2.3，2026.11.08，编号C2026-01'), '预算123456.78元，版本v1.2.3，2026.11.08，编号C2026-01');
assert.equal(numberedText('网址 https://example.invalid/1.条款 和 `1.代码`'), '网址 https://example.invalid/1.条款 和 `1.代码`');
assert.equal(numberedText('1、首项\n  2、次项'), '1、首项\n  2、次项');
assert.equal(numberedText(null), '');
assert.ok(formatters.markdown('1、提交；2、验收').includes('；<br>2、'));
assert.ok(formatters.markdown('| 条款 |\n| --- |\n| 1、提交；2、验收 |').includes('<td>1、提交；<br>2、验收</td>'));
assert.ok(!formatters.markdown('1、<script>；2、<img src=x>').includes('<script>'));
console.log('详情编号换行检查通过：10 项。');
