# frontend · 前端模块

本目录是项目的**前端模块**，与 Python 后端（`src/tender_extractor/`）完全解耦。
前端成员只需修改本目录，无需了解 Python。

## 文件说明

| 文件 | 页面 | 功能 |
|------|------|------|
| `app.css` | 全站 | 设计系统：色板、排版、按钮、表格、表单、卡片与响应式规则 |
| `shell.js` | 全站 | 注入左侧导航外壳（品牌、主操作、七个入口、本机状态） |
| `index.html` | 需求解析 | 上传采购需求报告，查看结构化字段、复核清单、模板映射与审核报告 |
| `fill.html` | 填写工作台 | 逐字段核对 / 编辑、选模板、生成前预览、自定义字段，生成招标文件 |
| `templates.html` | 模板库 | 内置 / 自有模板的列表、上传、重命名、设默认、删除、占位符体检与空值预览 |
| `preview.css` · `preview.js` | 填写工作台 / 模板库 | 共用的 A4 纸张预览样式与结构化 blocks 渲染器 |
| `compare.html` | 文档比对 | L2 双文档比对：上传招标文件 + 投标文件，查看符合性比对结果 |
| `projects.html` · `projects.js` · `projects.css` | 项目工作台 | 按采购项目归档任务、查看历史结果、归档与恢复 |
| `company.html` · `company.js` | 企业档案 | 维护资质、业绩、人员与证明材料索引 |
| `settings.html` · `settings.js` | 设置 | 模型接入、第三方复核、抽取阈值与输出目录的可视化覆盖 |
| `前端开发任务书.md` | — | 模板库扩展 / 生成前预览 / 自定义字段三项改造的需求、接口约定与验收清单 |

技术形态：纯 HTML/CSS/JS（无 CDN、无构建工具、无运行时依赖），直接改源码即可生效。

## 页面骨架

新增页面时复制这套骨架即可获得统一的左侧导航与视觉规范（样式全部来自 `app.css`，不要另写一套配色）：

```html
<link rel="stylesheet" href="/assets/app.css">
...
<div class="shell">
  <main class="main">
    <div class="main-inner">
      <div class="page-head"><h1>页面标题</h1><p>一句话说明</p></div>
      <!-- 页面内容 -->
    </div>
  </main>
</div>
<script src="/assets/shell.js"></script>
```

`main-inner` 的变体：`wide`（1320，工作台类页面）、`narrow`（820，设置类表单）。
图标一律用内联 SVG，不引入图标库；衬线标题走 `--font-serif`，正文走 `--font-sans`。

## 本地预览

前端由后端 Web 服务托管，启动方式（在**项目根目录**执行）：

```bash
start.bat web                  # 一键启动（自动准备环境）
# 或
python -m tender_extractor.web --no-browser --port 8765
```

默认地址：`http://127.0.0.1:8765`

服务会自动从本目录读取页面文件；修改后**刷新浏览器**即可看到效果（无需重启服务）。

也可以把前端指向任意其他目录（例如单独 clone 前端做预览）：

```bash
set TENDER_FRONTEND_DIR=D:\path\to\your\frontend
python -m tender_extractor.web
```

## API 契约（勿随意改动）

前端依赖以下接口，路径与语义**保持稳定**；需要新接口时先在后端 `web.py` 实现，再更新本表：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 服务与模型状态 |
| POST | `/api/extract?filename=...` | 上传文件并创建抽取任务（body 为文件二进制） |
| GET | `/api/jobs/{id}` | 任务状态（queued/running/done/error，含 stage 进度） |
| GET | `/api/jobs/{id}/result` | 抽取结果（字段 + 证据 + 置信度） |
| GET | `/api/jobs/{id}/report` | 审核报告（Markdown 渲染数据） |
| POST | `/api/compare` | 创建比对任务（multipart：`tender` + `bid` 两个文件） |
| GET | `/api/jobs/{id}/compare` | 比对结果（要求/响应/判定） |

### 模板库与预览（v0.2 新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/templates` | 模板列表：内置（`builtin: true`，只读）+ 用户上传 |
| POST | `/api/templates?filename=...` | 上传模板，body 为 `.docx` 二进制；可选 `name`/`category`/`method`/`version`；201 返回 meta |
| GET | `/api/templates/{id}` | 模板详情：`placeholders` / `unmapped` / `missing_fields` |
| GET | `/api/templates/{id}/preview` | 模板空值预览（结构同 job preview） |
| POST | `/api/templates/{id}/rename` | `{"name": "..."}`，内置模板返回 400 |
| POST | `/api/templates/{id}/default` | 设为默认模板 |
| DELETE | `/api/templates/{id}` | 删除模板，内置模板返回 400 |
| GET | `/api/jobs/{id}/fields?template_id=...` | 字段视图；响应新增 `template`（当前模板）与 `unmapped_placeholders` |
| POST | `/api/jobs/{id}/preview` | 生成前预览：`{"template_id"?, "values", "missing_mode", "custom_fields"}` → `{template, stats, missing, sections, warnings}` |
| POST | `/api/jobs/{id}/generate` | 请求体新增 `template_id` 与 `custom_fields`；响应仍为 `.docx` 二进制 |

**模板存储位置**：内置模板在仓库 `templates/compiled/`；用户上传的模板落在 `data/templates/<template_id>/`（`source.docx` + `compiled.docx` + `meta.json`），默认模板记在 `data/templates/default.json`。不要手改 `templates/compiled/`。

**预览 blocks 结构**：`sections[].blocks[]` 中段落为 `{type:"paragraph", style, segments[]}`，表格为 `{type:"table", rows[][]}`；单元格含 `colspan` / `rowspan`，被合并覆盖的位置是 `{"skip": true}`。`segments[].kind` 取 `text` / `value` / `missing`，后两者带 `name`（占位符名）。

响应字段结构以 `src/tender_extractor/web.py` 的 `Handler` 实现为准。

### 项目类型选择（v0.3 新增）

`GET /api/jobs/{id}/fields` 增加三个**可选** query：`method` / `category` / `org_form`，合法取值由后端 `config/field_scopes.json` 的 `axes` 下发（例：`method=inquiry&category=goods&org_form=self`）。不传时保持旧行为（只按原文推断组织形式标 `na`）；非法取值返回 400 `{"error": "..."}`。

响应新增：

- `scope`：`{method, category, org_form, complete, na, na_tags, labels, extra_fields, conflicts, axes, suggested, template_hint}`。`axes` 供三个下拉渲染；`suggested` 是从原文推断的初值（业务类型不推断，必须由用户选）；`template_hint` **只推荐、不自动切换模板**；`extra_fields` 是类型专属字段，前端转成 `custom_fields` 的 `target=append`，生成时写进文末「补充条款」表。
- 每个 `groups[].items[]` 新增 `na_reason` / `na_tag`；`label` 可能按业务类型覆写（如 `contract_period` 在货物类显示「合同交货期限」），但 `key` 与 `template` 不变。
- 顶层新增 `template_hint`（等同 `scope.template_hint`）。

字段口径（哪些字段不适用、标签怎么改、各类型补哪些字段、冲突提示）全部在 `src/tender_extractor/config/field_scopes.json`，改口径不用改代码；抽取层仍抽全量字段，证据链不变。

## 修改规范

1. 不要改动 API 路径与请求方式；渲染逻辑随便改；
2. 保持「无 CDN、无外部依赖」约束（文档不出内网的设计要求）；
3. 提交前用 `start.bat web` 自测一遍上传与比对流程；
4. 涉及新字段展示时，先确认后端 JSON 里确实有该字段。
