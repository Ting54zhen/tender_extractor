# frontend · 前端模块

前端使用 **npm + Vue 3 单文件组件 + Vite 多页面构建**，沿用已有 CSS。七个页面独立挂载，普通链接跳转，保留 `/`、`/fill`、`/templates`、`/projects`、`/company`、`/settings`、`/compare` 及 `.html` 别名、查询参数和全部 `/api/*`。

## 目录与拆分规则

```text
frontend/
├── *.html                   # 七个构建入口：元数据、挂载点、main.js 引用
├── src/
│   ├── pages/<page>/         # XxxPage.vue 布局、use_<page>_page.js 状态、main.js 挂载
│   ├── components/
│   │   ├── layout/           # AppShell：导航与品牌
│   │   ├── fields/           # FieldEditor、CustomFieldDialog：字段展示与草稿弹窗
│   │   ├── preview/          # DocumentPreview、FillPreviewPane：安全 blocks 渲染与面板
│   │   └── templates/        # TemplatePickerDialog：模板选择弹窗
│   ├── composables/          # 可复用的有状态逻辑，如任务轮询与销毁清理
│   ├── services/             # 同源 API、文件下载
│   ├── utils/                # 纯格式化、字段状态、存储、弹窗等通用函数
│   ├── config/               # 设置与档案的声明式表单定义
│   └── styles/               # 既有 app/preview/projects.css；pages/ 存页面样式
├── public/assets/            # 原样复制的 Vue MIT 许可与第三方声明
├── scripts/                 # 生产资源闭合检查
├── vite.config.js           # 七入口、回环监听与 /api 代理
├── package.json             # 固定直接依赖与开发命令
├── package-lock.json        # 全部依赖版本、官方源与完整性摘要，必须提交
└── dist/                    # 构建结果，忽略，不手改
```

页面负责组合组件与业务状态；可复用组件通过 props 接收数据、emit 通知操作，不直接抓取页面 DOM 收集值。独立面板和弹窗拆为语义组件；跨页面复用逻辑进入 composables/services/utils。仅当前页面需要的业务逻辑保留在该页面目录，不为了复用而建立全局状态。人工编辑、AI 建议、原文证据分开管理。新增或修改的代码附中文注释说明设计意图。

共用 CSS 不改变既有类语义；页面样式放 `styles/pages/`，通过对应 main.js 引入。不增加 Router、Pinia、大型组件库或图标库；图标使用本地内联 SVG。文档、模型和接口内容只用文本插值或安全格式化，禁止编译为 Vue 模板。金额保持字符串交给后端 Decimal 处理。

## 安装与运行

开发机需要 Node.js `^20.19.0 || >=22.12.0`（推荐当前 LTS）和 npm。在项目根目录：

```powershell
Set-Location frontend
npm ci
npm run dev
```

开发页面为 http://127.0.0.1:5173，Vite 热更新；另一个终端在项目根启动 `python -m tender_extractor.web --no-browser`，默认 8765。Vite 将 `/api/*` 代理到本机 8765，因此在 5173 上使用原有页面与接口即可。更改后端端口时同步调整 vite.config.js 的代理。两种服务默认只监听 127.0.0.1。

生产或按 Python 同源方式联调，先在 frontend 执行：

```powershell
npm run build
npm run check
```

然后在根目录启动 `python -m tender_extractor.web`，访问 http://127.0.0.1:8765。Python 仅分发 `frontend/dist`；修改源码后重新 build 并刷新，无须重启 Python。不要双击 HTML 或把源码目录交给生产静态服务器。`npm run preview` 仅用于本地查看构建结果；使用业务接口仍需本机 Python。

`TENDER_FRONTEND_DIR` 可覆盖为包含七个 HTML 与 assets/ 的**构建目录**。源码缺构建时返回明确提示，不会退回源码或自动联网安装。EXE 打包脚本会先 build/check，仅将 dist 打入包内 static；运行 EXE 的用户不需要 Node/npm。

## 依赖、许可与离线边界

直接依赖固定为 Vue **3.5.45**、Vite **8.3.6**、官方 Vue 插件 **6.0.10**，三者均为 MIT。Vue 是浏览器运行库，另外两者在开发/构建阶段使用。Vue 许可与 Vite 生成的 modulepreload 辅助代码许可见 public/assets/VUE-LICENSE.txt、VITE-LICENSE.txt；构建时复制进 dist 与 EXE。生产运行无 CDN、无 npm 请求和远程字体；默认文档处理仍留在本机。

首次 `npm ci` 下载开发依赖需要联网，不发送采购文档；安装后可离线 build。`.npmrc` 固定官方源、精确版本、禁用自动安装脚本与自动审计请求；依赖升级时审查许可证与锁文件，并重跑构建及回归。不要提交 node_modules、dist、真实文档或密钥。

## 验证

```powershell
# frontend 内，先构建
npm run build
npm run check             # 7 个入口、递归 JS/CSS 引用与许可证
npm run test:browser      # 本机夹具 + Chrome/Edge，19 项页面行为回归
# 根目录
python -m pytest tests -q # 静态分发测试需要先完成上面的 build
node scripts/check_browser.mjs
```

浏览器脚本使用 Node 原生 CDP，无测试框架依赖。check_browser 默认测试已打包 EXE；源码联调使用 `TENDER_CHECK_URL` 指向隔离本机服务、`TENDER_SAMPLE` 指向脱敏报告，避免污染真实任务。截图保存在已忽略的 data/。800ms 是预览防抖间隔，端到端耗时以实测为准。

## API 契约（勿随意改动）

需求入口分为三个可刷新、可复制链接的视图，共用现有 Vue 多页面入口：`/`（需求工作台卡片列表，支持状态筛选 / 搜索 / 刷新）、`/?view=new`（新建解析，支持输入文字 / 拖放报告 / 原有提示词）、`/?job=<id>`（需求详情，运行中恢复轮询，结束后显示字段 / 待复核 / 模板映射 / 审核报告）。`/index.html` 支持相同查询参数。新建解析的可选 `project_id` 用于预选有效项目。

文字以 UTF-8 `文字采购需求.txt` 二进制提交到原 `/api/extract`，与报告共用清洗和证据回链。若选择了报告，本次只解析报告；文字草稿保留，移除报告可恢复输入。文件 / 文字都受 50MB 限制，空白文字不可提交。

新建解析的输入框是对话式入口：点击「开始解析」或按 Enter 先把文字交给 `/api/chat/intent` 做意图分类——纯需求正文直接创建解析任务；「查看结果 / 缺失了什么 / 生成招标文件 / 下载 / 解析记录 / 删除」等指令在会话流里内联执行，任务进度、结果摘要、待复核清单与下载入口以卡片形式出现在会话里。`policy.risk=high` 的动作（生成 / 删除）先出确认卡，卡片上方气泡写明动作与对象摘要，按钮只有「确认」与「取消」；取消只回固定话术、不发请求，确认后才调用既有接口。带文件时跳过分类直接解析。所有动作都调用既有接口，完整字段表与原文证据仍在 `/?job=<id>` 详情页；解析完成后不再自动整页跳转。

**对话分流与边界（11/9）**：`POST /api/chat/intent` 只收 `text`（字符串，去空白后 1–2000 字符），非字符串 / 空白 / 超长返回 400；分类组件异常时降级为 200 `unknown`，不让对话入口报错中断。判定优先级：关键词规则（含 ≤40 字的口语动作：「出一份标书 / 弄个招标文件 / 还差啥 / 上次那份报告的结果 / 再解析一次」）→ 本地模型白名单兜底（≤200 字且不像需求正文就交模型理解，不再要求出现指令提示词；离线或模型异常仍走原启发式）→ 可确认的采购需求正文兜底 → `unknown`。>200 字的长文本按需求正文处理，正文里出现的「删除 / 生成招标文件」不触发指令。模型明确判 `unknown` 或输出越界时都不被启发式改写成有副作用的动作。允许：界面按意图调用既有接口、只展示接口返回的数据、副作用动作二次确认。不允许：模型产出字段值 / 证据 / 结论、模型直接触发动作、猜测代填、把无依据的话当事实。超出范围用固定文案明确告知能力边界并写明「未执行任何操作」。`matched_by` 在会话里显示为「规则判定 / 本地模型兜底 / 兜底判定」徽标；结果卡固定附「模型输出不作为事实、需结合原文证据人工复核」提示。话术与标签集中在 `src/services/chat_policy.js`。对话里输入本地文件路径（如 `D:\...\xx.docx`）时，前端优先用后端返回的 `target` 槽位（旧后端没有该字段时回落到本地 `detectLocalPath`），改用 `source_path` 交给本机服务代读，回显实际文件名并创建任务；路径不存在 / 类型不支持由后端明确报错。`extract` 但 `target` 为空的短指令（如「帮我解析那个文件」）只做反问，不创建任务——这是「路径句被打包成 55 字正文后判过短」的修法。

**表达层（11/9）**：`POST /api/chat/answer` 把已经算好的结果改写成自然中文，仅接受展示型场景（`result` / `missing` / `jobs`），其余场景（帮助、越界、删除确认）必须保持固定话术。模型只返回 `{"answer": "..."}`，后端做四道校验：数字必须出现在结果数据里、不得出现承诺性措辞、不得出现中文数字、长度不超过 `max_chars` 的 2 倍；任一不通过就回落固定话术（`matched_by=fallback`）。模型不可用、健康检查不通过、调用超时同样回落，因此表达层永远不会阻断结果展示。配置见 `settings.json` 的 `chat_summary`（`enabled` / `max_chars` / `timeout_seconds`，缺省启用、320 字、20 秒）。前端先渲染固定话术与卡片，再异步请求总结并原地替换气泡文本，`via` 追加「模型总结」以便与规则判定区分。

抽取任务 `GET /api/jobs/{id}` 与 `POST /api/extract` 的任务响应新增 `progress: int`（0–100），比对任务保持原结构。进度是流程里程碑：排队 0、开始读取 5、文档已读取 15、类型识别完成 25、规则抽取完成 35、模型补缺完成 65、证据修复完成 75、复核完成 85、字段处理完成 90、准备保存 95、成功保存结果 100。模型等待期间不自动递增；跳过的文档完成审核结果保存后为 100，失败保留最后阶段百分比；已结束的历史任务恢复后同样显示 100。进度不代表剩余时间，运行中的百分比无需新增 DB 字段。

前端依赖以下接口，路径与语义**保持稳定**；需要新接口时先在后端 `web.py` 实现，再更新本表：

| 方法 | 路径                          | 说明                                                     |
| ---- | ----------------------------- | -------------------------------------------------------- |
| GET  | `/api/health`               | 服务与模型状态                                           |
| POST | `/api/extract?filename=...` | 上传文件并创建抽取任务（body 为文件二进制）；也可带 `source_path=<绝对路径>` 由本机服务代读磁盘文件——仅回环地址启用，扩展名限 .docx/.pdf/.txt、≤50MB，路径不存在/类型不符返回 400 明确报错 |
| POST | `/api/chat/intent`          | 对话入口意图分类：`{"text": "..."}`（1–2000 字符，非字符串 / 空白 / 超长 400）→ `{"intent","matched_by","keyword","target","policy"}`；`target` 是动作对象槽位，`extract` 时回 `{"type":"path"\|"text","value":...}`，说不出对象时为 `null`；`policy` 是执行策略 `{"risk":"low"\|"medium"\|"high"\|"none","action":"execute"\|"ask"\|"refuse","reason":"..."}`，界面只按 action 分流（ask 反问、refuse 拒答、execute 执行），其中 `risk=high` 的 execute 先出确认卡、用户确认后才执行，风险分级由本地规则表决定而非模型；只分类不执行动作，意图白名单 extract / list_jobs / result / missing / generate / download / delete / help / unknown；规则优先且规则命中不查模型健康，规则未命中且文本像指令时才由本地模型兜底，越界 / 异常降级 `unknown`，模型不产出任何事实值 |
| POST | `/api/chat/answer`          | 对话表达层：`{"intent","question","context"}` → `{"answer","matched_by","model","reason"}`；`intent` 仅允许 result / missing / jobs，其它 400；模型不可用或数字/措辞校验不通过时返回固定话术（200，`matched_by=fallback`），不阻断结果展示 |
| GET  | `/api/jobs/{id}`            | 任务状态（queued/running/done/skipped/error，抽取任务含 stage 与 progress 整数百分比） |
| DELETE | `/api/jobs/{id}` | 删除已结束的需求解析记录与该任务字段确认值；200 `{"deleted":"<id>"}`，不存在 404，排队 / 运行中 409，比对任务 400。保留所属项目、其他任务及已保存的本机文档；前端确认成功后返回需求列表。 |
| GET  | `/api/jobs/{id}/result`     | 抽取结果（字段 + 证据 + 置信度）                         |
| GET  | `/api/jobs/{id}/report`     | 审核报告（Markdown 渲染数据）                            |
| POST | `/api/compare`              | 创建比对任务（multipart：`tender` + `bid` 两个文件） |
| GET  | `/api/jobs/{id}/compare`    | 比对结果（要求/响应/判定）                               |

### 模板库与预览（v0.2 新增）

| 方法   | 路径                                      | 说明                                                                                                                              |
| ------ | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/templates`                        | 模板列表：内置（`builtin: true`，只读）+ 用户上传                                                                               |
| POST   | `/api/templates?filename=...`           | 上传模板，body 为 `.docx` 二进制；可选 `name`/`category`/`method`/`version`；201 返回 meta                              |
| GET    | `/api/templates/{id}`                   | 模板详情：`placeholders` / `unmapped` / `missing_fields`                                                                    |
| GET    | `/api/templates/{id}/preview`           | 模板空值预览（结构同 job preview）                                                                                                |
| POST   | `/api/templates/{id}/rename`            | `{"name": "..."}`，内置模板返回 400                                                                                             |
| POST   | `/api/templates/{id}/default`           | 设为默认模板                                                                                                                      |
| DELETE | `/api/templates/{id}`                   | 删除模板，内置模板返回 400                                                                                                        |
| GET    | `/api/jobs/{id}/fields?template_id=...` | 字段视图；响应新增 `template`（当前模板）与 `unmapped_placeholders`                                                           |
| POST   | `/api/jobs/{id}/preview`                | 生成前预览：`{"template_id"?, "values", "missing_mode", "custom_fields"}` → `{template, stats, missing, sections, warnings}` |
| POST   | `/api/jobs/{id}/generate`               | 请求体新增 `template_id` 与 `custom_fields`；响应仍为 `.docx` 二进制                                                        |

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
2. 保持「Vue 本地分发、无 CDN、无外部资源」约束，不新增 Router / Pinia / 大型组件库或其他构建工具；
3. 提交前先 `npm run build`，再用 `start.bat web` 自测上传与比对流程；
4. 涉及新字段展示时，先确认后端 JSON 里确实有该字段。
5. 文档和接口原文使用文本插值；不要把它们编译为 Vue 模板或直接传给 `v-html`，保留证据与人工复核状态。
