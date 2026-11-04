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
| 前端 | 纯 HTML/CSS/JS，**无 CDN、无构建工具、无外部依赖** |
| 测试 | pytest（共 15 个测试文件 / 101 项，当前全绿） |

**三条硬约束（改动前必须知道）：**

1. **本地闭环优先**：文档默认不出本机，Web 默认只监听 `127.0.0.1`；
2. **零第三方 Web 依赖**：不引入 Flask/FastAPI/前端框架，保证内网部署与商用合规；
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

| 文件 | 页面 | 职责 |
|------|------|------|
| `shell.js` | 全站 | 注入左侧导航外壳（品牌、主操作、入口、本机状态） |
| `app.css` | 全站 | 设计系统：色板、排版、按钮、表格、表单、卡片与响应式规则 |
| `index.html` | 需求解析 `/` | 上传采购需求报告 → 展示结构化字段、证据、复核清单、模板映射、审核报告 |
| `fill.html` | 填写工作台 `/fill` | 逐字段核对/编辑，历史值复用、AI 建议采纳、标的清单导入、生成招标文件 |
| `compare.html` | 文档比对 `/compare` | L2 双文档比对：招标文件 + 投标文件 → 符合性比对结果 |
| `projects.html` · `projects.js` · `projects.css` | 项目工作台 `/projects` | 按采购项目归档任务、查看历史结果、归档/恢复、项目级 zip 导出 |
| `company.html` · `company.js` | 企业档案 `/company` | 维护资质、业绩、人员与证明材料索引 |
| `settings.html` · `settings.js` | 设置 `/settings` | 模型接入、第三方复核、抽取阈值与输出目录的可视化覆盖 |
| `README.md` | — | 前端协作说明、页面骨架与 API 契约表 |

**前端与后端的解耦方式：**

`web.py` 的静态目录解析顺序为：

```
环境变量 TENDER_FRONTEND_DIR  →  项目根 frontend/  →  包内 static/（兜底）
```

页面路由与文件同名（`/` → `index.html`、`/fill` → `fill.html`、`/compare` → `compare.html`、`/projects` → `projects.html`、`/company` → `company.html`、`/settings` → `settings.html`），`/assets/*` 映射到本目录内的 css/js。

因此前端成员**只改 `frontend/` 下的文件、刷新浏览器即生效**，不需要重启服务、不需要碰任何 Python 代码。

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

**提交前自检：**

```bash
git status --short          # 不应出现 data/、参赛材料/ 下任何文件
git diff --cached --stat    # 确认变更范围符合预期
```

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

`<scope>` 取模块名：`frontend`、`pipeline`、`web`、`rules`、`templates`、`graph`。

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

### 8.3 提交信息规范

```
<type>(<scope>): <中文简述>

type:  feat | fix | refactor | docs | test | chore
scope: 模块名，如 frontend、pipeline、cli、graph
```

示例：`feat(frontend): 上传区支持拖拽与进度条`

### 8.4 接口变更流程

改 API 路径/字段结构时：**后端先改 `web.py` → 同步更新 `frontend/README.md` 契约表 → 通知前端 → 前端适配 → 同一次 PR 或紧邻 PR 合并**。禁止单方面改契约。

---

## 九、本地开发环境

### 9.1 首次准备

```bash
# 1. 克隆
git clone git@github.com:Ting54zhen/tender_extractor.git
cd tender_extractor

# 2. 一键准备环境（创建 .venv、安装依赖、检查模型服务）
start.bat setup --dev

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
| 前端独立预览 | `set TENDER_FRONTEND_DIR=<自定义目录>` 后启动 web |

### 9.3 输出位置

抽取结果统一写到 `data/结果审核报告/`：`-结果.json`、`-审核报告.md`、`-平台草稿.json`。

---

## 十、常见问题

| 问题 | 原因与解法 |
|------|-----------|
| `Host key verification failed` / `Permission denied (publickey)` | Git 自带 MSYS2 ssh 不支持中文用户名路径。已全局修复：`git config --global core.sshCommand "C:/Windows/System32/OpenSSH/ssh.exe"` |
| `UnicodeDecodeError: 'gbk' codec` 警告 | Windows 终端 GBK 解码测试子进程输出所致，不影响测试结果；可设 `PYTHONIOENCODING=utf-8` 减少噪音 |
| Web 打开是旧页面 | 确认 `STATIC_DIR` 指向 `frontend/`（启动日志可见），或检查是否设置了 `TENDER_FRONTEND_DIR` |
| 模型抽取结果为空 | 先跑 `start.bat health` 确认 Ollama 与模型名；再确认 `settings.json` 的 `llm.model` |
| 抽取被判定为「非招标文件」 | `doc_type` 前置拦截，属预期行为；调试可加 `--allow-non-tender` |

---

## 十一、当前状态与下一步

> 本节是**进度的唯一来源**。完成一项就从「下一步」移到「已完成」；新增待办加在这里并写清启动条件。

**产品主线：** AI 招标文件制作软件 —— 输入采购需求报告，输出完整招标文件。

**已完成：**

- 模块化改造：前后端解耦、`data/` 隔离、标准模板入库；GitHub 同步与 SSH 环境修复。
- 抽取引擎三道防线：规则层（不依赖模型）→ 模型补缺 + 证据核验 → 第三方复核；含跨字段勾稽、文档类型前置校验、校验失败回喂（≤2 轮）。
- 招标文件生成器：字段占位符回填 + **结构化表格回填**（主要商务要求表、标的清单简表/详表、每标的一张技术参数附表，共 8 张附表）；编制规范默认值兜底；采购人地址回退推断。
- Web 工作台：项目归档 `/projects`、填写页 `/fill`（历史值复用 / AI 建议采纳 / 标的清单导入）、企业档案 `/company`、设置 `/settings`；「生成招标文件」按钮、PDF 导出、项目级 zip 导出均已上线。
- SQLite 持久化（`storage.py`）：项目、任务、字段确认值、设置覆盖，重启可恢复。
- 免 Python 分发：`build_exe.py` 单文件 exe + `check_exe.py` 自动验收。
- 测试 101 项 / 15 个文件，全绿。

**下一步（优先级从高到低）：**

1. **合规审查 v1**：排他性/倾向性条款规则库（判定 + 分级 + 修改建议）。当前源码中尚无任何实现，是从零开始的新模块；启动前需先定判定口径与误报容忍度。
2. **模板库扩展**：货物 / 服务 / 工程 × 公开招标 / 竞争性磋商，配套模板管理。依赖先拿到各类模板的原件。
3. **评标办法生成器**：评分细则初稿。依赖第 1 项的合规口径（评分项不得含倾向性）。
4. **多标段 / 分包识别**：`platform_adapter` 目前固定输出单包 `P1`。
5. **脱敏样本补充**（`samples/`）：每类模板至少一份，供回归评测。
