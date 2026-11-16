# DEVELOPMENT · 开发说明与协作约定

> 项目：**招标需求解析组件（tender-extractor）**
> 定位：面向招投标场景的可信信息抽取引擎 —— 输入采购需求报告（.docx / .pdf / .txt），输出结构化字段 JSON、审核报告、知识图谱与平台草稿。
> 本文档说明**每个目录/文件干什么**、**谁在维护**、**协作怎么约定**。新成员请先读完本文。

---
## 一、技术栈与设计约束

| 项 | 内容 |
|----|------|
| 语言 | Python 3.10+（当前开发环境 3.12） |
| 运行时依赖 | `python-docx`、`docxtpl`（PDF 需可选安装 `pdfplumber`） |
| 模型接入 | 本地 Ollama（默认）或任意 OpenAI 兼容接口 |
| Web 服务 | Python 标准库 `http.server`，**不引入 Web 框架** |
| 前端 | Vue 3.5.45 + npm + Vite 8.3.6（MIT），SFC 多页面，沿用 CSS，**生产无 CDN** |
| 测试 | pytest（当前验证结果见第十一节） |

**三条硬约束（改动前必须知道）：**

1. **本地闭环优先**：文档默认不出本机，Web 默认只监听 `127.0.0.1`；
2. **Web 资源本机分发**：后端不用 Flask/FastAPI，前端使用已授权的 Vue + npm + Vite，保留 MIT 许可；不使用 CDN、Vue Router、Pinia 或大型组件库；
3. **证据可回链**：任何模型产出的值都必须能回到原文片段，禁止无证据字段。

---

## 二、目录职责总览

```
.
├── src/tender_extractor/   # 后端核心包（抽取引擎 + Web 服务）
├── frontend/               # 前端模块（团队成员修改界面只碰这里）
├── tests/                  # 单元测试
├── scripts/                # 启动器与工具脚本
├── examples/               # 对外使用示例
├── templates/              # 招标文件标准模板（生成器素材）
├── samples/                # 脱敏样本（进 git，团队共享）
├── data/                   # 真实数据与输出（不进 git，本地私有）
├── docs/                   # GitHub Pages 项目展示页
├── pyproject.toml          # 包元数据与依赖
├── start.bat               # Windows 一键启动
├── README.md               # 项目介绍与快速开始
├── AGENTS.md               # AI 协作规则（任何 AI 会话先读这一份）
└── DEVELOPMENT.md          # 本文档
```

| 目录 | 职责 | 维护者 | 进 git |
|------|------|--------|--------|
| `src/tender_extractor/` | 抽取引擎全部逻辑：解析、抽取、校验、复核、图谱、Web API | 后端 | ✅ |
| `frontend/` | 页面与交互：上传、字段展示、复核清单、报告、比对页 | 前端 | ✅ |
| `tests/` | 单元测试，改动引擎后必须全绿 | 全员 | ✅ |
| `scripts/` | 一键启动器与一次性工具脚本（见下） | 全员 | ✅ |
| `examples/` | 最小可运行的使用示例，对外演示 API 用法 | 后端 | ✅ |
| `templates/` | 招标文件模板：源模板（单花括号占位符）+ `compiled/`（jinja2 渲染版，由 `prepare_template.py` 生成） | 产品 | ✅ |
| `samples/` | **脱敏后**的样本，供团队协作与回归测试（脱敏标准见 `samples/README.md`） | 全员 | ✅ |
| `data/` | 真实采购样本、抽取输出、投标文件 —— **含真实单位信息，严禁提交** | 各人本地 | ❌ |
| `docs/` | GitHub Pages 站点（项目展示页），对外公开，不放内部文档 | 产品 | ✅ |
| `AGENTS.md` | AI 协作规则：三条硬约束、编码与测试要求、禁止清单；任何 AI 会话先读 | 全员 | ✅ |

---

## 三、后端模块职责（`src/tender_extractor/`）

### 1. 入口层

| 文件 | 职责 |
|------|------|
| `__init__.py` | 包入口与对外 API：导出 `Extractor`、`ExtractionResult`，提供一行式 `extract_document()` |
| `__main__.py` | `python -m tender_extractor` 直启入口，转发到 `cli.main()` |
| `cli.py` | 命令行入口（`extract` / `generate` / `inspect` / `health`），用于联调、批量跑样本与回归 |
| `web.py` | 本地 Web 服务：上传 → 后台线程抽取 → 轮询状态；API 与前端目录托管（见第四节） |

### 2. 文档解析层

| 文件 | 职责 |
|------|------|
| `document.py` | 文档解析：docx/pdf/txt → 有序 `Block` 列表，保留段落/表格先后顺序与合并单元格 |
| `doc_type.py` | 抽取前文档类型识别：拦下简历、论文、说明书等无关文档，避免硬抽编造 |
| `sections.py` | 章节切分：按「一、」「（一）」「1.」等中文编号识别，**不依赖 Word 标题样式** |

### 3. 抽取层

| 文件 | 职责 |
|------|------|
| `rules.py` | 确定性规则抽取：项目名、预算、期限、商务要求、采购标的、付款分期、资格/售后/交付条款、★/▲条款 |
| `llm_extract.py` | 本地大模型抽取与「校验失败回喂」修复（≤2 轮），只补缺、不编造；内置证据核验 |
| `relation_extract.py` | 列表型实体/关系抽取：资格条件、评分点、废标条款；**按类别分别调用模型**（三次独立请求），让小模型每轮只专注一类 |

### 4. 模型接入层

| 文件 | 职责 |
|------|------|
| `llm_client.py` | 客户端工厂：解析 `settings.json` 的 `llm.provider`（ollama / openai）并创建客户端 |
| `llm_common.py` | 共用类型、错误、健康检查结构、JSON 提取（单独成模块避免循环依赖） |
| `ollama_client.py` | Ollama 客户端，仅标准库直连本机服务 |
| `openai_client.py` | OpenAI 兼容客户端，适配任意 `/v1/chat/completions` 服务（DeepSeek/通义/Kimi 等） |

### 5. 质量保障层（可信三防线）

| 文件 | 职责 |
|------|------|
| `cleaning.py` | 文本清洗与值归一化：控制字符、空白、金额/日期/百分比/电话/中文大写金额 |
| `validation.py` | 判定中心：字段归一化、格式校验、**证据回链**、跨字段勾稽（标的合计 vs 预算等） |
| `reviewer.py` | 第三方模型独立复核：support / reject / replace 三结论，只做减法与修正、不新增字段 |
| `compliance.py` | 合规审查 v1 规则层：排他性/倾向性条款识别（品牌限定、特定区域业绩、本地化机构、规模门槛、厂家授权），输出等级、原文引文与修改建议；**只提示不改写** |
| `models.py` | 跨模块数据模型 dataclass（`Block` / `Document` / `Candidate` / `FieldValue` / `Evidence` / `ExtractionResult`） |

### 6. 编排与输出层

| 文件 | 职责 |
|------|------|
| `pipeline.py` | 编排核心 `Extractor`：解析 → 规则 → LLM → 合并 → 清洗 → 校验 → 回喂 → 复核 → 输出 |
| `platform_adapter.py` | 平台适配：抽取结果 → 科瑞招标平台 AI 草稿 JSON（平台契约见文件头注释） |
| `report.py` | 审核报告渲染：字段值 + 证据 + 冲突 + 校验错误 + 条款 + 模板回填 + 审计信息 |
| `doc_generator.py` | **招标文件生成**：抽取结果 + compiled 模板 → 完整 `.docx`；字段占位符回填 + 结构化表格渲染（商务要求表、标的清单、每标的一张技术参数附表），缺失字段标记「待补充」，不编造 |

### 7. 知识图谱层

| 文件 | 职责 |
|------|------|
| `graph_builder.py` | 字段结果 + 关系结果 → RDF 三元组 / Turtle 文本（**零新增依赖**，可存 `.ttl` 用 Protégé 打开） |
| `graph_queries.py` | 纯 Python 图查询（不依赖 SPARQL 引擎）：生成《投标符合性自查表》《废标风险预警清单》《评分点-证明材料映射》 |

### 8. L2 投标能力（**当前缓行**，保留代码但暂停迭代）

| 文件 | 职责 |
|------|------|
| `bid_extract.py` | 投标文件响应抽取：投标方能提供的资质/能力/业绩/承诺（与 `relation_extract` 对称） |
| `compliance_matcher.py` | 符合性比对：招标要求 vs 投标响应逐条对齐，输出 satisfied/partial/missing 偏离表 |
| `bid_writer.py` | 投标书生成器：招标文件 + 企业资源 → 投标书初稿 |
| `company_profile.py` | 企业资源档案：投标书生成所需的企业侧素材库 |

> ⚠️ **产品主线已收敛为「AI 招标文件制作」**，投标侧模块仅保留不删除，避免误投入。

### 9. 配置（`src/tender_extractor/config/`）

| 文件 | 职责 |
|------|------|
| `fields.json` | 抽取字段定义：字段分组、key/label/模板占位符、类型、枚举值、别名、所属章节 |
| `settings.json` | 运行参数：`llm`（抽取模型）、`review`（复核模型）、`extraction`（阈值）、`output`（输出目录） |
| `company_profile.example.json` | 企业档案示例；使用时复制为 `company_profile.json`（已被 git 忽略） |

### 10. 持久化与基础设施

| 文件 | 职责 |
|------|------|
| `storage.py` | SQLite 持久化（`projects` / `jobs` / `field_values` / `settings` 四表）：短连接 + WAL，适配多线程 Web |
| `paths.py` | 运行时路径解析：源码运行取工程根、打包后取 exe 同目录，数据统一落 `data/`（兼容 PyInstaller 单文件） |

---

## 四、前端模块（`frontend/`）

| 目录/文件 | 职责 |
|------|------|
| `*.html` | 七个 HTML 入口，仅元数据、挂载点和模块脚本 |
| `src/pages/<page>/` | 页面 SFC、独立业务 composable 与 main.js |
| `src/components/` | 导航、字段卡片、自定义字段弹窗、模板选择、结构化预览与面板 |
| `src/composables/` | 共享任务轮询与生命周期管理 |
| `src/services/` · `src/utils/` · `src/config/` | 同源请求/下载、纯工具、声明式表单 |
| `src/styles/` | 原有 CSS；页面样式在 pages/，不重新设计视觉 |
| `public/assets/` | Vue MIT 许可与第三方声明，原样复制到产物 |
| `package.json` · `package-lock.json` · `vite.config.js` | npm 精确依赖、锁文件、七入口与本机 API 代理 |
| `dist/` | 仅生产 HTML/JS/CSS/许可，忽略，不手改、不提交 |

源码开发先在 frontend 执行 `npm ci`，运行 `npm run dev` 使用 127.0.0.1:5173 热更新，并将 `/api/*` 代理到 Python 的 8765。生产或 Python 同源联调先 `npm run build`，再刷新 8765；Python 无需重启。Node/npm 仅开发机需要，不能在运行时从 CDN 或 npm 拉取资源。

静态根：显式 `TENDER_FRONTEND_DIR`（指向构建目录）优先；源码默认 `frontend/dist/`，冻结 EXE 默认包内 `static/`。缺构建不回退到源码、不暴露 node_modules。打包脚本先 build/check，再仅收集 dist。

页面路径与 `.html` 别名、查询参数及 `/api/*` 保持原约定；不引入 Router/Pinia/大型组件库。详细拆分规则、命令及目录见 `frontend/README.md`。

**API 契约（改动需双方确认）：**

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 服务与模型状态 |
| GET | `/api/settings` | 当前设置与本地覆盖值 |
| POST | `/api/settings` | 保存设置覆盖 |
| GET | `/api/company` | 企业档案 |
| POST | `/api/company` | 保存企业档案 |
| POST | `/api/extract` | 上传文件创建抽取任务（query：`filename`、`use_llm`、`allow_non_tender`、`project_id`） |
| GET | `/api/jobs` | 任务列表（query：`project_id`） |
| GET | `/api/jobs/{id}` | 任务状态与进度 |
| GET | `/api/jobs/{id}/fields` | 字段视图（含确认值、历史值、模板映射） |
| GET | `/api/jobs/{id}/result` | 抽取结果 JSON |
| GET | `/api/jobs/{id}/report` | 审核报告 Markdown |
| GET | `/api/jobs/{id}/download/{json\|md\|draft}` | 下载结果 JSON / 审核报告 / 平台草稿 |
| POST | `/api/jobs/{id}/confirm` | 保存人工确认的字段值（`{"values": [...]}`） |
| POST | `/api/jobs/{id}/suggest` | 为缺失字段起草 AI 建议值（需人工采纳） |
| POST | `/api/jobs/{id}/generate` | 按确认值生成招标文件并下载 `.docx` |
| POST | `/api/jobs/{id}/export/pdf` | 招标文件转 PDF（LibreOffice / Word COM） |
| POST | `/api/jobs/{id}/analyze` | 知识图谱风险分析（废标预警 / 符合性自查 / 评分点映射） |
| POST | `/api/jobs/{id}/bid` | 生成投标书初稿（L2 缓行） |
| POST | `/api/compare` | 创建比对任务（multipart：`tender` + `bid`） |
| GET | `/api/jobs/{id}/compare` | 比对结果 |
| GET / POST | `/api/projects` | 项目列表（`?status=`）/ 新建项目 |
| GET | `/api/projects/{id}` | 项目详情（含任务列表） |
| POST | `/api/projects/{id}/{archive\|restore\|rename\|delete}` | 项目归档 / 恢复 / 重命名 / 删除 |
| POST | `/api/projects/{id}/export` | 项目内已完成任务打包 zip 下载 |

---

## 五、脚本与示例

| 文件 | 职责 |
|------|------|
| `start.bat` | Windows 一键启动入口（转调 `scripts/launch.py`） |
| `scripts/launch.py` | 启动器：准备 `.venv`、检查依赖与模型服务、交互菜单、透传参数给 CLI（`setup` / `test` / `web` / `extract` / `generate` …） |
| `scripts/prepare_template.py` | 把源模板的单花括号占位符转换为 docxtpl（jinja2）语法，产出 `templates/compiled/` |
| `scripts/analyze_template.py` | 只读分析：占位符清单、跨 run 分布、与 `fields.json` 的字段映射差异 |
| `scripts/inspect_template.py` | 只读工具：打印 docx 模板的段落/表格结构，用于分析标准模板 |
| `scripts/verify_generated.py` | 只读验证：生成的 `.docx` 是否有未渲染占位符、待补充标记统计 |
| `scripts/check_compiled.py` | 只读诊断：compiled 模板里的占位符转换结果 |
| `scripts/check_fields.py` | 只读诊断：打印指定字段的抽取结果 |
| `scripts/debug_fields.py` | 只读诊断：定位字段原文上下文（所在段落/表格行及前后文） |
| `scripts/analyze_missing.py` | 只读统计：抽取结果的缺失字段明细 |
| `scripts/build_exe.py` | PyInstaller 打包单文件 exe（产物 `dist/`） |
| `scripts/check_exe.py` | 打包后验收：在独立目录运行 dist 产物，验证页面与 DB 落位 |
| `scripts/bid_gen_demo.py` | 投标书生成演示（L2 缓行，仅供研究） |
| `scripts/eval_final.py` | 关系抽取效果评估（资格/评分点/废标条款的抽取输出） |
| `examples/extract_demo.py` | 对外最小示例：演示 Python API 的调用方式 |

---

## 六、测试（`tests/`）

| 文件 | 覆盖范围 |
|------|---------|
| `test_document.py` | 文档解析（段落/表格顺序、合并单元格） |
| `test_doc_type.py` | 文档类型识别与拦截 |
| `test_cleaning.py` | 文本清洗与归一化 |
| `test_money_decimal.py` | 金额 Decimal 解析与格式化 |
| `test_pipeline.py` | 编排主链路（规则 → 合并 → 校验） |
| `test_llm_pipeline.py` | LLM 抽取、回喂修复、证据核验 |
| `test_reviewer.py` | 第三方复核逻辑 |
| `test_compliance.py` | 合规审查规则层：五类条款命中、否定语境不误报、证据回链、分级计数 |
| `test_platform_adapter.py` | 平台草稿 JSON 映射 |
| `test_cli_output.py` | CLI 输出文件与参数行为 |
| `test_openai_client.py` | OpenAI 兼容客户端（mock 请求/响应） |
| `test_report.py` | 审核报告渲染 |
| `test_doc_generator.py` | 招标文件生成：填充、缺失标记、参数校验 |
| `test_phone_extraction.py` | 联系电话抽取与去空格归一化 |
| `test_storage.py` | SQLite 持久化（项目/任务/确认值/设置，重启恢复） |
| `test_web_api.py` | Web API（任务/确认值/历史值/AI 建议/投标书/图谱/zip 导出） |

**约定：改动 `src/` 后必须跑全量测试且全绿才能提交。测试细则见 `AGENTS.md` 第 7 节。**

```bash
python -m pytest tests -q
```

---

## 七、数据与安全约定

| 规则 | 说明 |
|------|------|
| `data/` 严禁提交 | 内含真实政府采购文件（真实单位名、联系人），已在 `.gitignore` 隔离 |
| `samples/` 必须脱敏 | 单位名改「某单位」、人名/电话/地址打码，但**保留章节结构与表格形态** |
| 抽取输出不进仓库 | `data/结果审核报告/` 全部在忽略名单内 |
| 参赛材料不进仓库 | `参赛材料/` 含个人信息，已忽略 |
| 模型输出不作最终事实 | 所有结果均需人工复核，产品层面已内置复核清单 |

**提交前自检：**按 8.6 清单执行；其中 `git status --short` 不应出现 `data/`、`参赛材料/` 下任何文件。

---

## 八、协作开发约定

### 8.1 分工边界

| 角色 | 负责目录 | 禁止改动 |
|------|---------|---------|
| 后端 | `src/`、`tests/`、`scripts/` | `frontend/`（除接口协商） |
| 前端 | `frontend/` | `src/`、`tests/` |
| 产品/文档 | `docs/`、`README.md`、`DEVELOPMENT.md`、`samples/` | 代码目录 |

### 8.2 分支模型（简化 GitHub Flow）

**长期分支只有 `main` 一条。**

```
main   ← 随时可运行、可打包；不直接 push，只接受 PR
```

不设 `develop`：2–3 人规模，多一条长期分支只会多一层合并成本。

**短期分支命名**

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feature/<scope>-<简述>` | 新功能 | `feature/compliance-check` |
| `fix/<scope>-<简述>` | 修缺陷 | `fix/rules-phone-match` |
| `refactor/<scope>-<简述>` | 不改行为的重构 | `refactor/web-router` |
| `docs/<简述>` | 文档 | `docs/deploy-macos` |
| `chore/<简述>` | 依赖、脚本、打包 | `chore/build-exe-pathsep` |

`<scope>` 取 8.3 表格里的模块名（`frontend`、`web`、`rules`、`generator` …）。

**生命周期**：从最新 `main` 拉出 → 开发 + 自测 → 提交 PR → 1 人 review → Squash merge → 删分支。
分支存活不超过 2 周，超过说明任务该拆。

**按第十一节计划的分支安排**

| 计划项 | 分支名 | 基线 | 依赖 | 主要改动 |
|---|---|---|---|---|
| 0. 现有工作区收尾 | `chore/worktree-cleanup` | `main` | 无 | 全部（先清干净再开新分支） |
| 1. 合规审查 v1 | `feature/compliance-check` | `main` | 无 | 新增 `compliance*.py`、`tests/` |
| 2. 模板库扩展 | `feature/template-library` | `main` | 无（可与 1 并行） | `templates/`、`config/fields.json` |
| 3. 评标办法生成器 | `feature/scoring-generator` | `main` | **等 1 合并**（复用合规口径） | 新增 `scoring*.py` |
| 4. 多标段 / 分包 | `feature/multi-lot` | `main` | 无 | `platform_adapter.py` |
| 5. 脱敏样本补充 | `chore/samples-round-1` | `main` | 建议随 2 一起 | `samples/` |
| 前端改造（持续性） | `feature/frontend-<简述>` | `main` | 无 | `frontend/` |

**两人并行怎么排**

- 第一周：A 开 1，B 开 2（互不碰文件）
- 第二周：A 继续 1 或转 4，B 开 3（前提是 1 已合并进 `main`）
- 前端只改 `frontend/`，任何时候都能并行，不参与上面的串行依赖

**发版：用 tag，不用 release 分支**

- 功能合并进 `main` 后打 `v0.x.0` → 跑 `build_exe.py` + `check_exe.py` → 产物传 GitHub Release
- 线上问题走 `fix/*` → PR → 合并 → 打补丁 tag `v0.x.1`

**PR 要求**：改 `src/` 必须带测试且 `pytest` 全绿；改 `frontend/` 附一张改动后的截图；改 API 契约按 8.4 同步 `frontend/README.md`。

### 8.2.1 日常流程：提交到哪、从哪拉

先说要去的方向：代码只有一个落脚点，就是 `origin`（`git@github.com:Ting54zhen/tender_extractor.git`）。拉代码从这里拉，推分支也往这里推，不分叉、不加第二个远程仓库。长期存在的分支只有 `main` 一条，所有工作都从 `main` 出发，最后合回 `main`。

每次开工，先切回 `main`，用 `git pull --ff-only` 把远端最新取下来。这里只允许快进，一旦出现分叉，说明有人绕过 PR 直接往 `main` 推过，先查清原因再继续。拿到最新代码后，从 `main` 开一条临时分支，例如 `git switch -c feature/compliance-check`，命名沿用 8.2 的前缀。之后的改动全部落在这条分支上，`main` 上不写代码。

开发过程中按 8.6 的清单自测，测试全绿再提交。提交时只添加自己确实改过的文件，不要用 `git add -A` 图省事；提交信息按 8.3 的格式写。提交完成后，把这条分支推到远端的同名分支，即 `git push -u origin feature/compliance-check`。`-u` 只有第一次需要，之后直接 `git push` 就行。注意推的是自己的临时分支，不是 `main`。

推送完成后到 GitHub 开 PR，方向是 `main` 接收、`feature/compliance-check` 提供改动，仓库页面的 compare 链接会自动带上这个方向。review 通过后用 Squash merge 合并，并顺手删掉远端的临时分支。

合并之后回到本地收尾：切回 `main`，再 `git pull --ff-only` 拿到刚合入的提交，用 `git branch -d` 删掉本地临时分支，最后 `git remote prune origin` 清掉远端已删除分支留下的引用。

如果分支还没合并、`main` 却已经前进了，不要用 merge 把历史搅成网状，先 `git fetch origin`，再 `git rebase origin/main`。冲突逐块解决，改完 `git add`，然后 `git rebase --continue` 继续；全部完成后用 `git push --force-with-lease` 更新自己的临时分支。这条命令只对自己的分支用，永远不要对 `main` 用。

还有几条边界要守住：永远从 `main` 开分支，不要从别人的 `feature/*` 拉；需要别人尚未合并的代码时，等它合进 `main`，或者事先约定好先后顺序；本地 `main` 只做两件事——拉取最新、当分支基线。明确禁止直接 `git push origin main`（首次建基线除外）、`git push --force`、以及在 `main` 上直接提交。

当前状态（2026-11-04）：`main` 已推送，远端分支为 `main` 和两条个人分支 `yc`、`hmy`；这两条个人分支只在本人开发时使用，合并回 `main` 仍然走 PR。`git config user.name` 目前是占位的 `Tender Platform Team`，提交不会关联到你的 GitHub 账号，建议改成自己的名字和邮箱。仓库保持 Private，未启用 GitHub 分支保护（Private + Free 计划不支持），「只接受 PR」目前靠约定执行。

### 8.3 提交信息规范

**格式**

```
<type>(<scope>): <简述>

<正文：为什么改、影响什么>

<footer：BREAKING CHANGE / Refs>
```

**type 七选一：**

| type | 用途 | 进 Release Notes |
|---|---|---|
| `feat` | 新增能力、页面、字段、接口 | 是 |
| `fix` | 修缺陷（规则误判、渲染错位、崩溃） | 是 |
| `refactor` | 不改外部行为的重构 | 是 |
| `perf` | 性能优化（大文档解析、批量抽取） | 是 |
| `test` | 只增改测试 | 否 |
| `docs` | 只改文档 | 否 |
| `chore` | 依赖、脚本、打包、目录清理 | 否 |

**scope 用仓库现有模块名，不自造：**

| scope | 对应代码 |
|---|---|
| `pipeline` | `pipeline.py` 编排、`models.py` 数据结构 |
| `rules` | `rules.py`、`cleaning.py`、`validation.py`、`reviewer.py` |
| `generator` | `doc_generator.py`、`templates/`、`scripts/prepare_template.py` |
| `web` | `web.py` 路由、任务调度、静态托管 |
| `storage` | `storage.py`、`paths.py`、SQLite 表结构 |
| `graph` | `graph_builder.py`、`graph_queries.py` |
| `frontend` | `frontend/` 全部文件 |
| `cli` | `cli.py`、`__main__.py` |
| `scripts` | `scripts/` 下的工具脚本 |
| `build` | `build_exe.py`、`check_exe.py`、`pyproject.toml`、`start.bat` |
| `docs` | `README.md`、`DEVELOPMENT.md`、`DEPLOYMENT.md`、`docs/` |
| `structure` | 目录搬迁、模块拆分 |

跨多个 scope 且没有主次时省略 scope：`chore: 清理打包中间产物`。

**简述：**中文、动词开头，写「改完是什么样」，不写「修改 / 更新 / 优化」这类空词，句末不加句号。

| 写法 | 评价 |
|---|---|
| `fix(rules): 修正项目联系方式误匹配与多号码电话校验` | ✅ 看得出改了什么 |
| `feat(generator): 招标文件结构化表格回填与编制规范默认值` | ✅ |
| `fix: bug`、`update code` | ❌ 无信息 |
| `feat: 各种改动` | ❌ 应拆分 |

**正文必须写的情况：**改了行为或契约；别人需要跟着做（重装依赖、重跑 `prepare_template.py`、迁移 DB）；有取舍或被否掉的方案。正文讲「为什么」，不复述 diff。

**footer：**

- `BREAKING CHANGE: <不兼容点>` —— 改 `/api/*` 字段、改模板占位符、改输出 JSON 结构时必须写；
- `Refs: #12` / `Closes: #12` 关联 issue；
- 禁止写真实项目名、单位名、金额、人名（`data/` 里的内容不进提交信息）。

**粒度：**

- 一次提交只做一件事，功能改动不夹带格式化或无关重命名；
- 单个 PR 建议 ≤ 400 行 diff，超过先拆任务；
- 分支上允许 `wip` 提交，PR 合并前 squash 成一条符合本规范的记录。

### 8.4 接口变更流程

改 API 路径/字段结构时：**后端先改 `web.py` → 同步更新 `frontend/README.md` 契约表 → 通知前端 → 前端适配 → 同一次 PR 或紧邻 PR 合并**。禁止单方面改契约。

### 8.5 共同开发注意点

| # | 注意点 | 具体做法 |
|---|---|---|
| 1 | 只用项目虚拟环境跑测试 | `.venv/Scripts/python.exe -m pytest tests -q`；系统 Python 缺 `docxtpl`，直接跑会出现 12 个收集错误 |
| 2 | 编码统一 UTF-8 | Windows 终端默认 GBK：跑脚本前设 `$env:PYTHONUTF8='1'`（`start.bat` 已设）；不要用 `>` 重定向生成含中文的源文件 |
| 3 | `data/` 各人本地 | 代码和测试里不出现 `data/` 下的具体路径，测试统一用 `tmp_path` |
| 4 | 不手改 `templates/compiled/` | 改源模板后重跑 `prepare_template.py`，compiled 是产物 |
| 5 | 契约同步 | 改 `web.py` 路由/字段 → 同 PR 更新 `frontend/README.md`；前端改完先对表 |
| 6 | 高冲突文件先打招呼 | `web.py`、`cli.py`、`config/fields.json` 多人易撞；按函数/字段块改，禁止整文件格式化 |
| 7 | 模型链路必须可离线测 | 测试不连网、不依赖 Ollama，一律 mock；手工验证再用 `start.bat health` |
| 8 | 金额只用 `Decimal` | 走 `cleaning.parse_money`，禁止 float 参与金额运算 |
| 9 | 证据不可放宽 | 新字段必须带 `block_id` + `quote`，且值真的出现在引文里；不为填充率好看放宽校验 |
| 10 | 不加运行时依赖 | 当前仅 `python-docx`、`docxtpl`（可选 `pdfplumber`）；要加先在 PR 里说明 stdlib 为何做不到 |
| 11 | 冲突不要整文件覆盖 | `git fetch` 后 rebase `main`，逐块合并；禁止 `--ours` / `--theirs` 一把梭 |
| 12 | 提交前先看 `git status` | `git add -A` 之前确认没有 `data/`、`dist/`、`build/`、`*.spec`、`参赛材料/` 混入 |

### 8.6 提交前自检清单

```bash
.venv/Scripts/python.exe -m pytest tests -q   # 必须 exit 0
git diff --check                              # 无行尾空白、无冲突标记
git status --short                            # 只含预期文件，无 data/、dist/、build/
git diff --stat                               # 变更范围与提交信息对得上
```

- [ ] 改了 `src/` → 有对应测试，没有删断言迁就代码
- [ ] 改了 `frontend/` → 浏览器实际点过一遍，PR 附截图
- [ ] 改了 API → `frontend/README.md` 契约表同步
- [ ] 改了模板 → 重跑 `prepare_template.py` 与 `verify_generated.py`
- [ ] 提交信息能覆盖 diff 里的全部改动，没有夹带

**PR 描述四行**：改了什么 / 为什么 / 怎么验证（命令与结果）/ 影响面（是否动契约、模板、DB）。

---

## 九、本地开发环境

### 9.1 首次准备

```bash
# 1. 克隆
git clone git@github.com:Ting54zhen/tender_extractor.git
cd tender_extractor

# 2. 一键准备环境（创建 .venv、安装依赖、检查模型服务）
start.bat setup --dev

# 仅源码网页开发需要 Node/npm；首次下载依赖，构建产物由 Python 本地分发。
npm --prefix frontend ci
npm --prefix frontend run build

# 3. 确认本地模型（默认配置）
ollama list        # 需要 qwen2.5:7b-instruct-q4_K_M 与 qwen2.5:1.5b-instruct
```

### 9.2 常用命令

| 场景 | 命令 |
|------|------|
| 抽取一份文件 | `start.bat extract "路径\文件.docx"` |
| **生成完整招标文件** | `python -m tender_extractor generate "文件.docx"`（输出 `.docx`） |
| 生成时留空而非标记 | `... generate "文件.docx" --missing empty` |
| 只跑规则不调模型 | `start.bat extract "文件.docx" --no-llm` |
| 查看解析结构 | `start.bat inspect "文件.docx"` |
| 检查模型服务 | `start.bat health` |
| 打开网页界面 | `start.bat web`（默认 `127.0.0.1:8765`） |
| 跑测试 | `start.bat test` 或 `python -m pytest tests -q` |
| 前端热更新开发 | `npm --prefix frontend run dev`（5173，API 代理到 Python 8765） |
| 前端生产构建/检查 | `npm --prefix frontend run build` / `npm --prefix frontend run check` |
| 前端自定义构建目录 | `set TENDER_FRONTEND_DIR=<自定义 dist 目录>` 后启动 web |

### 9.3 输出位置

抽取结果统一写到 `data/结果审核报告/`：`-结果.json`、`-审核报告.md`、`-平台草稿.json`。

---

## 十、常见问题

| 问题 | 原因与解法 |
|------|-----------|
| `Host key verification failed` / `Permission denied (publickey)` | Git 自带 MSYS2 ssh 不支持中文用户名路径。已全局修复：`git config --global core.sshCommand "C:/Windows/System32/OpenSSH/ssh.exe"` |
| `UnicodeDecodeError: 'gbk' codec` 警告 | Windows 终端 GBK 解码测试子进程输出所致，不影响测试结果；可设 `PYTHONIOENCODING=utf-8` 减少噪音 |
| Web 打开是旧页面 | 执行 npm run build，确认 `STATIC_DIR` 指向 `frontend/dist/`（启动日志可见），或检查是否设置了 `TENDER_FRONTEND_DIR` |
| 模型抽取结果为空 | 先跑 `start.bat health` 确认 Ollama 与模型名；再确认 `settings.json` 的 `llm.model` |
| 抽取被判定为「非招标文件」 | `doc_type` 前置拦截，属预期行为；调试可加 `--allow-non-tender` |

---

## 十一、当前状态与下一步

> 本节是**进度的唯一来源**。完成一项就从「下一步」移到「已完成」；新增待办加在这里并写清启动条件。

**产品主线：** AI 招标文件制作软件 —— 输入采购需求报告，输出完整招标文件。

**已完成：**

- 重新检测按钮主题跟随（11/11）：按最新要求让「重新检测」复用主按钮的背景、文字与悬停主题变量，浅色模式黑底白字、深色模式浅底黑字，两种主题继续无边框；同步前端说明及新建 / 详情 / 比对页的双色断言。`npm --prefix frontend run build`、`npm --prefix frontend run check` 通过（7 入口、25 本地资源），`node scripts/check_vue.mjs` 45/45 通过，深色截图已更新。未修改 Python 引擎，未重跑 Python 测试，未重建 EXE。
- 重新检测按钮与深色模式（11/11）：新建 / 详情 / 比对页「重新检测」统一黑底白字、无边框，保留原健康检测请求。侧栏品牌旁新增月亮 / 太阳主题切换按钮，390px 窄屏仍可用；默认浅色，`localStorage` 的 `tender.theme` 保存本机偏好，普通多页面跳转 / 刷新恢复，storage 事件同步其他标签页。七个 HTML 入口在样式和 Vue 前加载本机 `assets/theme-init.js`，避免主题恢复闪白；存储损坏时使用浅色，禁用存储时本次仍能切换并提示无法保存。全站卡片、表单 / 下拉、弹窗、状态 / 焦点、知识图谱改用主题颜色变量；文档预览保留白纸黑字和浅色待补充高亮。`npm --prefix frontend run build`、`npm --prefix frontend run check` 通过（7 入口、25 本地资源，新增前置主题脚本检查），`node scripts/check_detail_text.mjs` 10/10 通过，`node scripts/check_vue.mjs` 45/45 通过（新增 4 项：重新检测外观及请求、七页 / 新建 / 详情主题与持久化 / 布局 / 对比度、深色弹窗 / 下拉 / 图谱 / 纸面预览、390px 切换与损坏 / 禁用存储），无外部资源请求或浏览器异常。截图在已忽略的 `data/vue-browser-check/dark-*.png`；前端主题约定已同步 README。本轮未修改 Python 引擎，未重跑 Python 测试，未重建 EXE。
- 新建解析页精简页头（11/11）：按最新界面要求移除顶部「新建解析」h1 与说明段落，模型状态保留为右对齐工具栏，正文的居中引导语、说明、输入 / 上传区保持原功能；详情页继续显示标题。同步前端布局约定与五档屏宽回归：新建页检查无顶部 h1 / 空页头且正文入口存在，其余页面继续检查标题对齐。`npm --prefix frontend run build`、`npm --prefix frontend run check` 通过（7 入口、24 本地资源）；`node scripts/check_vue.mjs` 41/41 通过，截图已更新至 `data/vue-browser-check/layout-1440-new.png` 与 `layout-600-new.png`。未修改 Python 引擎，未重跑 Python 测试，未重建 EXE。
- 页面标题与宽度统一（11/11）：七个页面共用最大 1180px 的 `main-inner`（含左右留白），取消 820 / 1000 / 1180px 三种页面外框；所有 h1 距主区顶部 24px，统一桌面 32px / 窄屏 26px 字号，左右留白按断点为 40 / 24 / 16px。填写页复用公共页头，比对页与新建 / 详情页的模型状态放在标题右侧；新建解析与需求详情补齐页面主标题，居中引导语改为 h2。全站预留滚动条槽，长表单与短列表切换保持同一宽度；删除确认弹窗按窗口实际中点定位，修复滚动条槽引起的偏移。浏览器回归发现并修正自定义下拉展开 / 焦点色被 hover 覆盖；预览测试等待原生 close 事件后重开，保留并发旧响应断言。`npm --prefix frontend run build`、`npm --prefix frontend run check` 通过（7 入口、24 本地资源）；`node scripts/check_vue.mjs` 真实 Chrome 回归 41/41 通过，新增五档屏宽（1920 / 1440 / 1100 / 800 / 600px）的跨页面标题坐标、字号、容器宽度及横向溢出检查，覆盖七页与新建 / 详情视图。宽屏 / 窄屏截图在已忽略的 `data/vue-browser-check/layout-*.png`。前端布局约定已同步 `frontend/README.md`；本轮未修改 Python 引擎，未重跑 Python 测试，未重建 EXE。
- 状态配色与表单 UI 统一（11/8）：复用现有蓝 / 绿 / 红语义色，需求解析的「解析中 / 已完成 / 失败或已跳过」筛选、卡片标签 / 顶边和详情百分比进度保持一致，「全部」保留中性；固定状态占位与边框尺寸，切换仍无抖动。填写工作台的三项采购类型、缺失字段设置、自定义字段类型 / 占位符 / 候选选择共用原生下拉控件外观，统一圆角、内边距、CSS 箭头、蓝色焦点和禁用反馈；修正鼠标悬停覆盖焦点颜色，并用绿色就绪进度、蓝色人工填写标签补充语义提示。企业档案补齐全部输入框的文本类型与公共样式，覆盖基础信息、动态资质 / 业绩 / 人员及证明材料索引，增加小节蓝色标记、编辑焦点和材料占位提示，窄屏网格 / 材料行自适应。`npm run build`、`npm run check` 通过（7 入口、23 本地资源）；`node scripts/check_vue.mjs` 真实 Chrome 回归 36/36 通过（较前轮新增 4 项，覆盖三色及跳过状态、焦点 / 悬停 / 禁用、下拉候选保存、全部档案输入来源和 390px 窄屏，既有布局稳定与保存用例继续通过），无外部资源请求或浏览器异常。截图落已忽略的 `data/vue-browser-check/12-填写控件配色.png`、`13-企业档案统一输入框.png`、`14-需求状态三色.png`。本轮仅改前端与浏览器回归，未改 Python 引擎，未重跑 Python 测试，未重建 EXE。
- 需求 / 项目界面统一与记录删除（11/7）：需求详情「返回需求列表」和项目详情「返回列表」统一黑底白字；需求搜索在状态工具栏下显示「搜索结果 共xx条」，固定反馈占位、状态按钮宽度和滚动条空间，修复状态激活加粗、长列表 / 空筛选结果导致的抖动。浏览器标题按需求解析 / 新建解析 / 需求详情对应视图，新建解析不再选中侧栏需求解析。详情新增红字 / 淡红底 / 红边框删除按钮，使用居中确认弹窗，失败留窗、成功返回列表；新增 `DELETE /api/jobs/{id}` 删除已结束的抽取记录、确认值与内存缓存，保留项目、其他任务和本机文件，排队 / 运行中返回 409，比对任务返回 400，契约同步 `frontend/README.md`。项目删除改为同款居中美化弹窗，原提示文案不变，按钮为红色「确认删除」与「取消」，项目空态跨满网格并居中。`npm run build` / `npm run check` 通过（7 入口、23 本地资源）；真实 Chrome 夹具回归 32/32 通过（新增 5 项，含三档状态布局坐标一致、搜索计数、两类删除确认 / 取消 / 失败与成功、项目空态 / 弹窗居中、标题与导航）；全量 `python -m pytest tests -q --tb=short`（匹配现有依赖的 Python 3.14 + `.venv/Lib/site-packages`）175 项通过，新增 4 项 API 用例，既有弃用警告保留。截图在已忽略的 `data/vue-browser-check/09-项目删除确认.png`、`10-项目空状态.png`、`11-需求搜索计数.png`；未重建 EXE。
- 新建解析输入与上传体验修复（11/7）：文字框随内容自动增高 / 删减后收回，最低 72px、最高 250px，超出后框内滚动，关闭手动拉伸；窗口宽度变化时按换行重新计算高度。文件拖入输入区显示「松开即可上传需求报告」，提示层不占布局，跨子元素拖动保持稳定，离开 / 放下 / 取消清除。上传入口与文件卡片复用固定占位、长文件名省略、工具栏提示预留宽度，并为整页预留滚动条空间，修复上传 / 移除前后抖动。`npm run build` / `npm run check` 通过；`node scripts/check_vue.mjs` 27/27 通过（新增 3 项，覆盖自动高度与上限 / 缩回、拖入提示与跨子元素、1440 / 1100 / 800 三档上传与移除前后主元素坐标尺寸完全一致），截图落 `data/vue-browser-check/07-松开上传提示.png`、`08-上传后保持对齐.png`。本轮只改前端与浏览器回归，未改 Python 引擎，未重跑 Python 测试，未重建 EXE。
- 需求解析工作流调整（11/7）：`/` 改为与项目工作台一致的需求卡片列表，支持状态筛选、搜索、刷新与查看详情；`/?view=new` 独立新建解析视图支持文字输入、报告拖放 / 选择、原提示词与项目 / 模式 / 强制抽取选择。文字转为 UTF-8 TXT 沿用原证据链，选择报告时明确只解析报告并保留文字草稿；任务创建后跳转 `/?job=<id>`，刷新可继续轮询。详情顶部保留真实阶段百分比条，完成后 100% 展示参考图的统计 / 下载 / 字段证据 / 待复核 / 模板映射 / 审核报告；失败保留阶段和错误，非采购文档明确显示跳过及重新选择入口。后端增加可选阶段回调与任务 `progress` 字段，前端契约已同步；没有增加依赖或改变模型证据核验。全量 Python 171 项通过（新增 4 项，含文字证据 / 阶段 / 跳过 / 失败 / 重启恢复），`npm run build` / `npm run check` 通过（7 入口、22 本地资源），真实 Chrome 夹具回归 24/24，通过文字提交跳转、拖放、100% / 失败态、详情刷新恢复及三档响应式；截图位于已忽略的 `data/vue-browser-check/`。全量首轮遭一次本机连接被系统中止，完整重跑全绿；未重建 EXE。
- 模块化改造：前后端解耦、`data/` 隔离、标准模板入库；GitHub 同步与 SSH 环境修复。
- 抽取引擎三道防线：规则层（不依赖模型）→ 模型补缺 + 证据核验 → 第三方复核；含跨字段勾稽、文档类型前置校验、校验失败回喂（≤2 轮）。
- 招标文件生成器：字段占位符回填 + **结构化表格回填**（主要商务要求表、标的清单简表/详表、每标的一张技术参数附表，共 8 张附表）；编制规范默认值兜底；采购人地址回退推断。
- Web 工作台：项目归档 `/projects`、填写页 `/fill`（历史值复用 / AI 建议采纳 / 标的清单导入）、企业档案 `/company`、设置 `/settings`；「生成招标文件」按钮、PDF 导出、项目级 zip 导出均已上线。
- SQLite 持久化（`storage.py`）：项目、任务、字段确认值、设置覆盖，重启可恢复。
- 免 Python 分发：`build_exe.py` 单文件 exe + `check_exe.py` 自动验收。
- 合规审查 v1（`compliance.py`）：5 类排他性表述规则（品牌限定、特定区域业绩、本地化服务机构、规模门槛、厂家授权），每条命中带 `block_id` + 原文引文 + 等级 + 修改建议；只提示不改写、不阻断生成，已接入抽取结果 JSON 与审核报告第九节。
- 模板库扩展（第一批）：派生物类公开招标、服务类竞争性磋商两套模板并批量 compiled；`derive_template.py` 单遍最长匹配 + 占位符保护，修掉源模板 `{ 中标候选供应商推荐家数}` 前导空格导致的字段永久失配；另下载深圳三类范本作参考素材。
- 后端模板库：`template_library.py` / `template_tools.py`，`/api/templates/*` 六个接口（列表 / 上传体检 / 详情 / 空值预览 / 重命名 / 设默认 / 删除），用户模板私有落在 `data/templates/<id>/`，内置模板只读。
- 生成前预览：`preview.py` 把 compiled 模板解析成结构化 blocks（章节 / 段落片段 / 表格 colspan·rowspan·skip），`POST /api/jobs/{id}/preview` 毫秒级返回，不依赖 Word / LibreOffice。
- 自定义字段：`generate` 接受 `template_id` + `custom_fields`；`target=placeholder` 写入同名占位符（不覆盖人工值），`target=append` 在文末生成「补充条款」表；预览与成稿保持一致。
- 前端三项：`/templates` 模板库页、填写页模板切换与预览面板（点击片段跳回字段）、自定义字段增删改与本地暂存；顺手修 `collectValues()` 只取当前分组（P0-1）与标的清单导入按钮不出现（P0-2）。
- 契约同步：`frontend/README.md` 补齐 v0.2 接口表与 blocks 结构；新增 `preview.css` / `preview.js` 供两页共用。
- 填写工作台项目类型选择：`/fill` 顶部先选采购方式 / 业务类型 / 招标组织形式，选齐后按 `config/field_scopes.json` 下发不适用字段、标签覆写、类型专属字段、冲突提示与模板推荐；内置模板补 `category/method` 标签；三个选择按 jobId 存本地，切换类型不丢已填值；抽取层仍抽全量字段，证据链不变。`frontend/README.md` 已同步 v0.3 契约。
- 字段口径按真实样本校准：新增 `scripts/analyze_samples.py`，对 `data/采购需求样本` 的 22 份文件只提取章节、表头与栏目（不输出正文，结果落 `data/` 不入库）。结论：`是否允许进口产品`（19/22 份）、`核心产品`、`价格扣除比例与计算公式`、`分项权重` 属**通用栏目**，进 `extra_fields.by_all`；`品牌 / 型号 / 产地` 是投标人响应列而非采购人填写项，已从货物专属字段移除；工程类保留工程量清单、最高投标限价、质量保修、验收办法；磋商保留最后报价。原「施工资质 / 项目经理 / 缺陷责任期」无样本支撑，已删除或合并。
- 打包版更新：PyInstaller 6.22.3 于 11/5 14:03 重打包单文件 exe（14.6 MB），包含 v0.2 模板库 / 预览 / 自定义字段与 v0.3 项目类型选择、字段口径校准；`check_exe.py` 通过（页面 / 静态资源 / DB 落位），另在空目录独立运行验证 10/10（模板库页、填写页、预览静态资源、三套内置模板、frozen 下模板预览 7 节、预览路由注册），8765 / 8877 / 8878 端口均已释放。
- exe 新功能验收固化：`scripts/check_exe.py` 从 4 项扩到 14 项固定检查（新增 `/templates`、`/fill`、`preview.css/js`、`/api/templates`、页面内容关键字、三套内置模板、frozen 下模板空值预览 7 节、任务预览路由 404、DB 落位），独立空目录运行 14/14 通过；以后每次打包自动覆盖 v0.2 / v0.3 新链路。
- 浏览器实机回归：新增 `scripts/check_browser.mjs`，用 Node 原生 WebSocket 驱动真实 Chrome CDP（零 npm 依赖），空目录启动 exe、纯规则样本建任务，跑 `frontend/前端开发任务书.md` 第 9 节可自动化部分，22/22 通过：三页面渲染、v0.3 类型 gate 与选齐后 15 个字段、预览 20 表 / 97 片段 / 7 处合并单元格、改字段 debounce 后同步（实测约 4.6s，未达任务书 800ms 目标，已列入下一步）、点片段跨分组高亮、1440/1100/800 三档无横向溢出、自定义字段刷新持久化与任务隔离、无外部域名请求、控制台零报错；关键步骤改用 DOM 条件等待，连续两次重跑均 22/22；三张截图落 `data/browser-check/`（不入库）。运行：`node scripts/check_browser.mjs`。
- Vue 多页面迁移：七个页面分别挂载 Vue，保留原 URL / `.html` 别名、查询参数与全部 `/api/*` 契约；公共导航、字段卡片、结构化预览改为组件，全部既有 CSS 保留。最初本地全局脚本方案已被下述 npm + Vite 单文件组件方案替代；不使用 CDN、Vue Router、Pinia 或大型组件库。
- Vue 浏览器回归：`node scripts/check_vue.mjs` 19/19 通过（本机 HTTP 夹具 + 真实 Chrome，零 npm 包）；覆盖七页与别名、跨分组修改 / 清空、金额字符串、建议人工采纳、标的导入、预览合并表格 / 跨组定位 / 旧响应丢弃、自定义字段持久化 / 任务隔离、模板管理、设置 / 档案 / 项目、解析结果安全转义、上传 / 移除 / 轮询和比对 multipart 请求。补充用例发现并修复首页模板引用与 `file` 状态同名导致的上传状态冲突；19 项全绿且无外部资源请求 / 浏览器异常，截图落 `data/vue-browser-check/`（不入库）。
- Vue 真实后端联调：`TENDER_CHECK_URL` 指向隔离源码服务、`TENDER_SAMPLE` 指定合成脱敏报告后，`node scripts/check_browser.mjs` 22/22 通过；纯规则解析、三套真实模板、预览 20 表 / 97 占位符片段、字段同步 / 定位、自定义字段与响应式布局均已复验。修正旧验收脚本以真实 input 事件驱动 Vue，并等 DOM 更新 / 浏览器退出；本次「改字段 → 预览可见」约 1.5s，800ms 仍仅为防抖间隔。
- 本地资源与发布检查：前一阶段已增加真实标准库静态分发测试、Vue 本地资源与许可检查；npm 方案下改为验证散列构建资源、锁文件完整性和许可，替代旧 vendor 摘要及固定脚本列表。七页既有样式迁入 src/styles，打包与验收脚本同步采用 dist。
- npm 工程化与职责拆分：Vue 3.5.45、Vite 8.3.6、官方插件 6.0.10 均固定版本（MIT），提交 package-lock；七个 HTML 仅作入口，src/pages 下拆 Page.vue / 页面业务 composable / main.js，components 下拆导航、字段编辑、自定义字段弹窗、模板选择弹窗、预览面板与安全 blocks 渲染；共享逻辑分别进入 composables/services/utils/config，样式移至 styles/pages 与公共 CSS。删除旧全局脚本和重复资源，保留中文注释；三个原有公共 CSS 逐内容比对一致。
- npm 构建与分发验证：`npm ci --offline --ignore-scripts`、`npm run build`、`npm run check` 通过，7 个入口 / 21 个本地 JS/CSS 资源闭合、Vue 与 Vite 辅助代码许可证完整；Vite 开发模式实测 14 个页面路径（含别名）、7 个模块入口 / SFC 编译与本机 /api 代理通过。npm 仅用于开发和打包；Python 只分发 frontend/dist，EXE 仅收集 dist，缺构建明确提示，不回退源码；补充静态根、源码隔离、缺产物、兄弟目录穿越测试。
- npm 最终页面回归：拆出弹窗和预览面板后，`node scripts/check_vue.mjs` 再次 19/19 通过；合成脱敏报告 + 隔离真实源码服务下 `node scripts/check_browser.mjs` 22/22 通过，20 张预览表 / 97 片段、合并单元格、字段同步 / 定位、自定义字段、响应式均通过；预览同步实测 1534ms，无外部域名请求或控制台异常。截图落 data/，不入库。
- npm 打包准备：已实际调用 build_exe.build_frontend() 与 collect_data()，确认 build/check 通过且仅收集 dist；check_exe 改为自动发现散列资源、校验七入口 / 许可 / node_modules 隔离。仅验证打包准备，本轮未重建或验收新版 EXE；AGENTS、README、前端任务书及本说明已同步新架构与运行步骤。
- 全量 Python 测试：`python -m pytest tests -q --tb=short`（使用可用 Python 解释器及 `.venv/Lib/site-packages`）167 项 / 23 个文件全部通过，较上一轮新增 2 项；收集数已复核。首轮沙箱内出现一次本机连接被系统中止，沙箱外完整重跑通过，未修改断言。现有 pytest 插件与 re.split 弃用警告保留。

**下一步（优先级从高到低）：**

1. **npm/Vue 版 exe 发布验收**：多页面构建、需求列表 / 新建 / 详情、阶段百分比、三色状态 / 表单 UI、页面标题 / 宽度统一与深色模式已完成；新建解析按最新要求省略顶部 h1 与说明，保留正文引导。现有 EXE 仍是此前版本。启动条件：需要分发时先在 frontend 执行 npm ci，再运行 scripts/build_exe.py（自动 build/check）、scripts/check_exe.py（当前 18 项）及默认 EXE 模式的 scripts/check_browser.mjs；必须在空目录确认七入口、散列资源、MIT 许可及源码隔离，并复验文字 / 报告创建后跳转详情、真实本机模型等待期间百分比保持、完成结果 / 跳过 / 失败态、填写 / 档案控件样式、五档屏宽的页面标题 / 容器对齐，以及深色切换 / 前置主题脚本 / 跨页刷新记忆、重新检测与主按钮的双色配色一致性。
2. **浏览器回归剩余人工项**：此前真实源码服务 22 项与本轮 Vue 夹具 45 项自动化已通过（含自动高度 / 拖放提示 / 上传与状态筛选布局稳定、需求删除与项目美化弹窗、三色状态与进度、下拉框焦点、档案动态输入 / 窄屏布局、页面标题与宽度对齐，以及重新检测外观 / 请求、深色主题 / 纸面预览 / 本机偏好恢复 / 存储异常）；真实模板的错误文件提示（.pdf、空 .docx、超 50MB、无占位符）、上传→设默认→切换模板→生成整条链路、Word 检查自定义字段与 8 张附表仍需验收。模板删除回退、建议采纳 / 标的导入、跨组编辑 / 清空已由夹具覆盖，真实成稿还需核对。启动条件：准备脱敏报告 / 模板与可打开的 Word，截图落 `data/`。
3. **预览同步性能达标**：Vue 源码联调的「改字段 → 预览可见」实测约 1.5s（此前原生前端 exe 验收约 4.6s），尚未达到端到端 800ms 目标；800ms 防抖本身已占满该目标。启动条件：先分别计时 `preview.build_preview` 与 Vue 渲染，再评估防抖间隔及按 `template_id + mtime` 缓存解析结果；复测以 `scripts/check_browser.mjs` 的实测值为准。
4. **自定义字段服务端持久化**：目前仅 `localStorage` 按 jobId 暂存，换设备会丢。启动条件：确定 `storage.py` 表结构后加自定义字段表，并让 `/fields` 直接回传。
5. **预览样式归一化复核**：模板段落样式名是 `null3`，现按「第X章 / 一、 / 1.」文本特征判断层级；换一套章节结构不同的模板要重新验证 `preview._style_of()`。
6. **工程类模板**：需要独立字段体系（工程量清单、通用/专用合同条款），现有 53 字段覆盖不了。v0.3 已按业务类型补工程类专属字段（生成时写入文末「补充条款」），但正文合同章与工程量清单附表仍依赖先拿到工程类范本原件并单独设计模板。
7. **评标办法生成器**：评分细则初稿。依赖合规审查已有的判定口径（评分项不得含倾向性）。
8. **多标段 / 分包识别**：`platform_adapter` 目前固定输出单包 `P1`。
9. **脱敏样本补充**（`samples/`）：每类模板至少一份，供回归评测。
10. **合规审查 v2**（可选）：规则可配置化（从 `config/compliance.json` 读取）、误报反馈闭环、前端展示命中项。当前规则写在代码里，新增类别需改代码并发版。
11. **通用栏目进字段契约**：`是否允许进口产品 / 核心产品 / 价格扣除 / 分项权重 / 付款方式` 目前只能走「补充条款」，要真正填进品目表与标的清单，需在 `fields.json` 新增字段、补模板表格渲染与证据规则。属抽取契约变更，先确认再动。
