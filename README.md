# 招标需求解析组件（tender-extractor）

输入采购需求报告（.docx / .pdf / .txt），输出**结构化字段 JSON**、**审核报告**与**完整招标文件 .docx**。
既可作为招标平台后端的关键信息抽取部件（由平台的表单/模板渲染层完成回填），也可直接生成可编辑的招标文件初稿。

> 新机器部署、模型准备、多机隔离与打包分发：见 [DEPLOYMENT.md](DEPLOYMENT.md)
> 代码结构、模块职责与协作约定：见 [DEVELOPMENT.md](DEVELOPMENT.md)

处理链路：

```
DOCX 解析 → 文档类型校验（非招标/采购文件则提示并跳过）
          → 规则抽取 → 本地 LLM 补缺 → 合并去冲突
          → 数据清洗/归一化 → 字段与跨字段校验
          → 校验错误 + 原文证据回喂 LLM 修复（最多 2 轮）
          → 复核清单 + 审计信息 + 模板占位符映射
          → （可选）招标文件渲染：compiled 模板 + 字段回填 → 完整 .docx
```

## 项目结构

```
招标需求解析组件/
├── src/
│   └── tender_extractor/  # 后端抽取引擎（核心包）
│       ├── config/        #   fields.json（字段定义）、settings.json（运行配置）
│       ├── storage.py     #   SQLite 持久化（projects/jobs/field_values/settings/company）
│       ├── paths.py       #   数据目录解析（源码与打包后统一落 data/）
│       ├── bid_writer.py  #   投标书生成（资格响应/技术方案/废标自查）
│       ├── company_profile.py  # 企业资源档案
│       ├── graph_builder.py    # 知识图谱构建（RDF/Turtle）
│       ├── graph_queries.py    # 符合性自查/废标预警/评分点材料映射
│       └── *.py           #   其他核心模块（cli/document/pipeline/llm_*/report/web/...）
├── frontend/              # 前端页面（index/fill/compare/projects/company/settings + assets）
├── templates/             # 招标文件标准模板（广东省政府采购公开招标标准模板.docx）
├── data/                  # 本地数据，不进 git：tender.db / 结果审核报告 / 采购需求样本
├── samples/               # 脱敏样本（进 git，见 samples/README.md）
├── examples/              # 使用示例（extract_demo.py）
├── scripts/               # 工具脚本（launch.py、build_exe.py 打包、check_exe.py 验收、演示脚本）
├── tests/                 # 单元测试（pytest）
├── docs/                  # GitHub Pages 项目展示页
├── run_web.py             # PyInstaller 打包入口
├── pyproject.toml         # 项目元数据与依赖
├── README.md              # 本文档
└── start.bat              # Windows 一键启动
```

> 各目录与文件的详细职责、数据安全约定、分支与接口变更流程，见 **[DEVELOPMENT.md](DEVELOPMENT.md)**。

> `src/*.egg-info/` 与各目录下的 `__pycache__/` 是安装/运行自动生成的产物，已在 `.gitignore` 中忽略，无需手动管理。

## 文件职责

### 核心源码（`src/tender_extractor/`）

| 文件 | 作用 |
|------|------|
| `__init__.py` | 包入口与对外 API，导出 `Extractor`、`ExtractionResult`，提供一行式 `extract_document()` |
| `__main__.py` | `python -m tender_extractor` 直启入口，转发到 `cli.main()` |
| `cli.py` | 命令行入口，解析参数、构建配置覆盖并驱动抽取与报告输出 |
| `document.py` | 文档解析层，把 docx/pdf/txt 转成有序 `Block` 列表，保留段落/表格顺序与合并单元格 |
| `doc_type.py` | 抽取前文档类型识别，拦下非招标/采购文件，避免硬抽编造 |
| `sections.py` | 按中文编号切分章节并写回每个 `Block` |
| `rules.py` | 确定性规则抽取层，项目名/预算/商务条款等可直接读字段不依赖模型 |
| `llm_client.py` | 模型客户端工厂，解析 provider 别名并创建 Ollama/OpenAI 客户端 |
| `llm_common.py` | LLM 客户端共用类型、错误、健康检查结构与 JSON 提取工具（避免循环依赖） |
| `llm_extract.py` | 本地大模型抽取与"校验失败回喂"修复逻辑（只补缺不编造） |
| `ollama_client.py` | 本地 Ollama 客户端，仅用标准库直连本机 Ollama |
| `openai_client.py` | OpenAI 兼容客户端，适配任意 `/v1/chat/completions` 服务（DeepSeek/通义/Kimi 等） |
| `cleaning.py` | 文本清洗与值归一化（去控制字符/空白、金额/日期/百分比/电话/中文大写金额解析格式化） |
| `validation.py` | 字段归一化、格式校验、证据回链与跨字段勾稽 |
| `reviewer.py` | 第三方模型独立复核，以 support/reject/replace 三结论二次判断并兜底 |
| `models.py` | 跨模块数据模型 dataclass（`Evidence`/`Block`/`Document`/`Candidate`/`FieldValue`/`ExtractionResult`） |
| `pipeline.py` | 编排核心 `Extractor`：解析→规则→LLM→合并→校验→回喂→结构化输出 |
| `platform_adapter.py` | 平台适配层，把抽取结果转成科瑞招标平台 AI 草稿 JSON |
| `report.py` | 把抽取结果渲染成人工审核用的 Markdown 报告 |
| `doc_generator.py` | 招标文件生成：抽取结果 + compiled 模板 → 完整 `.docx`，缺失字段标记「待补充」 |
| `web.py` | 本地标准库 Web 服务，上传文档并轮询查看字段/复核/报告 |

### 配置文件（`src/tender_extractor/config/`）

| 文件 | 作用 |
|------|------|
| `fields.json` | 抽取字段定义：字段分组、key/label/template/类型/枚举值/别名/所属章节 |
| `settings.json` | 运行参数：LLM、复核(review)、抽取(extraction)、输出(output)各段默认设置 |

### 前端（`frontend/`，打包时映射为包内 `static/`）

| 文件 | 作用 |
|------|------|
| `index.html` | 上传与解析结果页（字段 / 待复核 / 模板映射 / 审核报告 / 图谱可视化） |
| `fill.html` | 填写页：原文抽取、历史值复用、AI 建议采纳、标的清单导入 |
| `compare.html` | 招标要求 × 投标响应比对页 |
| `projects.html` | 项目工作台：任务归档、批量导出 zip |
| `company.html` | 企业档案：资质 / 业绩 / 人员 / 证明材料 |
| `settings.html` | 可视化设置：模型、复核、阈值、输出目录 |

页面使用 Vue 单文件组件，按 pages/components/composables/services/utils/styles 拆分，通过 npm + Vite 构建。沿用现有 CSS，不引用 CDN；Python 与 EXE 仅提供构建后的本地资源，终端用户无需 Node/npm。详情见 [前端说明](frontend/README.md)。

### 脚本与示例

| 文件 | 作用 |
|------|------|
| `scripts/launch.py` | 一键启动器，准备 .venv 与依赖、检查模型服务，并透传参数给 CLI/Web/测试 |
| `examples/extract_demo.py` | 最小集成示例，演示调用 `extract_document` 并打印模板映射与待复核清单 |
| `start.bat` | Windows 启动入口，调用 `scripts/launch.py` 并处理菜单/退出码 |

### 测试（`tests/`）

| 文件 | 测试对象 |
|------|----------|
| `test_cleaning.py` | `cleaning` 清洗/归一化 |
| `test_cli_output.py` | `cli` 输出目录解析与重名自动追加 |
| `test_doc_type.py` | `doc_type` 文档类型前置校验与跳过逻辑 |
| `test_document.py` | `document` docx 解析（块顺序、章节、合并单元格） |
| `test_doc_generator.py` | `doc_generator` 模板回填与缺失字段「待补充」 |
| `test_llm_pipeline.py` | 用本地 mock Ollama 测试 LLM 链路，不访问外网 |
| `test_money_decimal.py` | 金额输出护栏与跨字段勾稽 |
| `test_openai_client.py` | `openai_client` 请求格式、健康检查与端到端抽取 |
| `test_phone_extraction.py` | 联系电话抽取与去空格归一化 |
| `test_pipeline.py` | 端到端规则层（不依赖 Ollama） |
| `test_platform_adapter.py` | `platform_adapter` 平台草稿适配 |
| `test_report.py` | `report` Markdown 审核报告渲染 |
| `test_reviewer.py` | `reviewer` 第三方复核三结论与安全边界 |
| `test_storage.py` | `storage` SQLite 持久化（项目/任务/确认值/设置，重启恢复） |
| `test_web_api.py` | Web API（项目/任务/确认值/历史值/AI 建议/投标书/图谱/zip 导出） |

## 1. 特性

- **默认本地闭环**：默认只访问本机 Ollama（`http://127.0.0.1:11434`），文档不出内网；也可切换到任意 OpenAI 兼容 API（`provider=openai`），用于云端模型或公司内网模型网关。
- **文档类型前置校验**：解析后先判断输入是不是招标/采购需求类文件；简历、论文、说明书等无关文档只返回提示，不进入规则或模型抽取。
- **规则优先**：项目名称、预算、期限、采购标的、付款分期等能从原文直接读到的字段由确定性规则产出；模型不可用时仍可运行（`--no-llm`）。
- **证据可回链**：每个值携带 `block_id` + 原文引文，便于平台展示和人工复核；多行值（标的清单、资格条款、★▲ 条款）按行回链，重排格式不被误判为改写。
- **实质性条款提取**：★（不满足即废标）/▲（重要）条款从采购需求中逐条抽出，成为可见字段，废标风险不再藏在正文里。
- **清洗后回喂**：金额、日期、比例、电话、枚举等先归一化；校验失败时把错误原因、允许值和原文片段一起回喂模型，只接受“能回链且通过校验”的修复。
- **冲突显式化**：规则与模型取值不一致时保留双方候选并标记 `conflict`，不静默覆盖。
- **交叉勾稽**：除单字段校验外，还做跨字段一致性检查——采购方式 × 评审方法（磋商只能综合评分、谈判/询价只能最低价）、招标组织形式 × 代理机构（自主招标不应有代理机构、委托招标必须识别到代理机构）、标的合计 × 预算、预付款 + 尾款 × 预算。
- **多标的口径**：需求报告的采购标的表常有多行，除单值「采购标的」外另出「采购标的清单」（逐项含数量/单价/金额/要求摘要），填写页可一键导入为多项标的。
- **不适用字段自动识别**：识别到自主（自行）招标时，代理机构整组字段标记「不适用」，不参与缺失统计与模板回填，也不占用就绪进度。
- **模板映射**：`template_values` 以中文占位符名为 key，直接对应标准招标文件模板中的 `{项目名称}`、`{预算金额}` 等。
- **招标文件生成**：`generate` 命令把抽取结果回填进标准模板，直接产出可编辑的完整 `.docx`；缺失字段标记「待补充」而不编造。除字段占位符外，还回填结构化表格——主要商务要求表（时间/地点/付款/验收/履约保证金）、采购标的清单（简表 + 详表，逐项数量/单价/总价/所属行业）、每项标的一张技术参数附表（★▲ 实质性条款逐条标注，无条款时回退为整体要求），实现真实招标文件的多附表效果。
- **编制规范默认值**：需求报告不写、但属招标文件必然字段的项（有效供应商家数=3、中标供应商数量=1、报价方式=总价、是否接受联合体=不接受），生成时用政府采购编制惯例兜底填充，优先级低于抽取值与人工值，减少「待补充」噪音。
- **可审计**：输出 `meta`、`audit`、`review`，记录模型、耗时、调用统计、修复轮次和复核优先级。
- **数据持久化**：项目、任务、字段确认值与设置覆盖全部写入本机 SQLite（`data/tender.db`）；关掉窗口或重启程序后，历史任务仍可查看与出文件。
- **项目工作台**：按采购项目归档任务，支持新建 / 打开 / 归档 / 恢复 / 重命名 / 删除；归档不删数据。
- **字段资产复利**：上一个项目确认过的采购人、代理机构、合同份数等，在新任务的缺失字段旁以绿色提示条出现，点「采用」秒填；已有可靠值的字段不被旧值干扰。
- **AI 建议值（不编造）**：原文没有、但模板需要的字段（合同条款类）可由模型起草建议值，黄色提示条展示并附理由，**采纳前不写入抽取结果、不参与模板回填**——三色并存：绿=历史值、黄=AI 建议、蓝灰=原文抽取。
- **投标书生成**：解析结果 + 企业档案 → 资格响应逐条映射证明材料（标出缺口）、技术方案按评分点起草、废标条款转提交前自查清单，产出投标书初稿 `.md`。
- **知识图谱风险分析**：抽取结果 + 关系抽取构建 RDF 图（Turtle 格式，Protégé 可直接打开），自动生成「投标符合性自查表 / 废标风险预警清单 / 评分点-证明材料映射」三类清单，网页内按类型分列可视化。关系抽取返回空时，废标条款回退到规则层「★ 实质性条款」，风险清单不会出现空表，`meta.fallbacks` 标明兜底来源。
- **企业档案**：资质 / 业绩 / 人员 / 证明材料索引一次录入，投标书生成自动引用；存本机数据库，逐项目复用。
- **导出体系**：招标文件 docx（内置）→ PDF（自动探测 LibreOffice / Word，缺转换器时给明确安装指引）；项目级一键打包 zip（结果 JSON + 审核报告 + 平台草稿 + 招标文件 docx）。
- **可视化设置**：模型接入、第三方复核、校验阈值、输出目录均可在网页里改，立即生效；与出厂默认不同的项标「已覆盖」，可一键恢复。
- **免 Python 分发**：`scripts/build_exe.py` 打包成单文件 `exe`，内嵌配置、前端与模板，双击即跑；数据落在 `exe` 同目录 `data/`，整个文件夹可拷贝给他人。

## 2. 快速开始

### 2.1 一键启动（Windows）

源码网页首次启动前，开发机安装 Node.js `^20.19.0 || >=22.12.0`，在根目录执行：

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
```

页面源码修改后重新构建并刷新；热更新开发用 `npm --prefix frontend run dev`（5173，API 代理到本机 Python 8765）。EXE 用户无需这些步骤。

双击 `start.bat`，或在本目录执行：

```bat
start.bat
```

首次运行会自动创建 `.venv` 并安装依赖，随后检查本机 Ollama，进入操作菜单（抽取 / 仅规则 / 健康检查 / 解析结构 / PDF 支持 / 测试）。之后启动复用同一环境，秒开；`pyproject.toml` 改动时会自动重装。

也可以跳过菜单，直接透传 CLI 参数：

```bat
start.bat extract "data\采购需求样本\1.docx"
start.bat extract "data\采购需求样本\1.docx" --no-llm
start.bat generate "data\采购需求样本\1.docx"     # 生成完整招标文件 .docx
start.bat health
start.bat setup --pdf
start.bat test
```

结果写入 `data/结果审核报告/`（`-结果.json` / `-审核报告.md` / `-招标文件.docx`）。需要自己管理环境时，按 2.2 手动安装。

### 2.1.1 打包成免 Python 的 exe（给不装环境的同事 / 现场演示）

```bat
.venv\Scripts\python scripts\build_exe.py      :: 产物 dist\招标需求解析.exe
.venv\Scripts\python scripts\check_exe.py      :: 验收：独立目录启动 + 页面 + 数据落位
```

exe 内嵌 `config/`、`frontend/dist/`、`templates/compiled/`；运行后在 exe 同目录生成 `data/`（`tender.db`、结果文件、`web.log`），整个目录可直接拷给他人。启动日志同时写 `data/web.log`，双击后窗口一闪而过时用它排障。`start.bat` 菜单第 8 项可直接启动已打包版本。企业档案、投标书生成、图谱分析等全部功能在 exe 中同样可用；PDF 导出依赖目标机器的 LibreOffice 或 Word，缺转换器时页面会给安装指引。

### 2.2 安装

```bash
python -m pip install -e .
# 可选：PDF 支持
python -m pip install -e ".[pdf]"
# 开发依赖
python -m pip install -e ".[dev]"
```

要求 Python 3.10+。核心依赖 `python-docx`（MIT）与 `docxtpl`（模板渲染，LGPL-2.1）。

### 2.3 准备本地模型

```bash
ollama serve
ollama pull qwen2.5:3b-instruct
python -m tender_extractor health
```

默认模型 Qwen2.5-3B-Instruct（Apache-2.0，约 1.9GB，4GB 显存可跑）。显存充足可换 7B，修改 `settings.json` 或命令行 `--model`。

### 2.4 接入 API（OpenAI 兼容，可选）

默认走本机 Ollama。如果要接云端 API 或公司内网的模型网关，把 `provider` 设为 `openai`，指向任何兼容 `POST {base_url}/chat/completions` 的服务，例如 DeepSeek、通义千问 compatible-mode、Kimi、智谱、硅基流动、OpenAI，以及 vLLM / LM Studio / One-API 这类自建网关。

```bash
# 推荐把 key 放环境变量，避免进入命令历史和日志
export TENDER_EXTRACTOR_API_KEY="sk-..."          # PowerShell: $env:TENDER_EXTRACTOR_API_KEY="sk-..."

tender-extract extract "需求报告.docx" \
  --provider openai \
  --base-url https://api.deepseek.com/v1 \
  --model deepseek-chat
```

先检查接口和 key 是否可用：

```bash
tender-extract health --provider openai \
  --base-url https://api.deepseek.com/v1 --model deepseek-chat
```

也可以把配置写进 `settings.json`：

```json
"llm": {
  "provider": "openai",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-chat",
  "api_key": "",
  "api_key_env": "TENDER_EXTRACTOR_API_KEY",
  "timeout_seconds": 120,
  "max_tokens": 2048,
  "json_mode": true
}
```

常见服务的 `base_url`：

| 服务 | base_url | 模型示例 |
|------|----------|----------|
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| Kimi | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `Qwen/Qwen2.5-7B-Instruct` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |

说明：

- `api_key` 直接写在配置里也可以，但优先用 `api_key_env`；默认依次读取 `TENDER_EXTRACTOR_API_KEY`、`OPENAI_API_KEY`。
- `base_url` 要包含版本路径（如 `/v1`），组件不会自动补全。
- `json_mode=true` 会发送 `response_format: {"type": "json_object"}`；服务不支持时自动去掉并重试一次。
- 模型输出仍要经过证据核验和字段校验，不会直接采信 API 返回。
- 401/403 会直接判为不可用；429/5xx 最多重试 2 次；`/models` 不可用的网关也能正常抽取。

> **合规提醒**：`provider=openai` 会把文档片段发送到该接口。涉密或内网资料请继续用 `ollama`，或把 `base_url` 指向内网自建网关。

### 2.5 抽取

```bash
# 完整链路（规则 + 本地模型 + 校验回喂）
# 默认输出到 D:\ClaudeCode\招标需求解析组件\结果审核报告\：
#   需求报告-结果.json、需求报告-审核报告.md
tender-extract extract "需求报告.docx"

# 无模型环境 / 回归测试（仍会写结果和审核报告，只是不调用模型）
tender-extract extract "需求报告.docx" --no-llm

# 指定其他输出目录
tender-extract extract "需求报告.docx" --output-dir "D:\某个目录"

# 只写 JSON，不生成审核报告
tender-extract extract "需求报告.docx" --no-md

# 显式指定文件名（此时不做自动改名，按给定路径写入）
tender-extract extract "需求报告.docx" -o "结果.json" --md "审核报告.md"

# 只看文档解析结构
tender-extract inspect "需求报告.docx"

# 确认不是招标/采购文件、但仍要强制抽取
tender-extract extract "其他文件.docx" --allow-non-tender
```

输出目录按 `--output-dir` > `settings.json` 的 `output.dir` > 组件内置默认目录的顺序解析。未指定 `-o` / `--md` 时，文件名取输入文件名（去扩展名）加 `-结果.json` / `-审核报告.md`；若同名文件已存在，自动追加 `-2`、`-3`，不覆盖历史结果。显式传 `-o` / `--md` 时，只写文件名（如 `-o 结果.json`）也会放进输出目录；带目录的路径按原样写入。

退出码：`0` 成功；`1` 运行失败；加了 `--fail-on-review` 且存在高风险复核项时返回 `2`；识别为非招标/采购需求文件并跳过抽取时返回 `3`。

### 2.6 网页界面

不想敲命令时，可以启动本地网页界面：

```bash
python -m tender_extractor.web                      # 自动打开 http://127.0.0.1:8765/
python -m tender_extractor.web --port 8766 --no-browser
```

在 `start.bat` 菜单里选 `7)`，或执行 `start.bat web` 等价。

界面能力：

- 拖拽或选择文件（.docx / .pdf / .txt，≤50MB），切换「完整链路 / 仅规则」，可勾选强制抽取；
- 结果按「字段 / 待复核 / 模板映射 / 审核报告」分页展示，字段带置信度、来源和原文证据；
- 文档类型校验未通过时说明原因，并提供「仍要抽取」入口；
- 抽取在后台线程执行、页面轮询进度，长文档不会卡住请求；
- 结果同时落盘到 `结果审核报告/`（`-结果.json`、`-审核报告.md`、`-平台草稿.json`），页面可直接下载；
- 解析结果工具栏一键**生成投标书初稿**（引用企业档案，展示资格覆盖数与材料缺口）、**图谱风险分析**（三类清单 + 可视化图谱 + `.ttl` 下载）、**导出 PDF**；
- 填写页缺失字段旁自动出现**历史值复用**（绿色）与 **AI 建议值**（黄色，需采纳）提示条；
- 顶部导航：**项目工作台**（`/projects`：任务归档 + 项目级 zip 导出）、**企业档案**（`/company`：资质/业绩/人员素材库）、**设置**（`/settings`）；上传时可指定所属项目；
- 历史任务通过 `/?job=<id>` 重新打开，也可从工作台点进，重启程序后仍可查看并继续出文件。

实现只用标准库 `http.server`，无 Web 框架依赖；前端是单个内联 CSS/JS 的 HTML，不引用任何 CDN，可离线部署。默认只监听 `127.0.0.1`，文档不出本机；确需局域网访问时显式传 `--host 0.0.0.0`。

## 3. 输出结构

CLI 默认把结果 JSON 和人工审核报告写入 `D:\ClaudeCode\招标需求解析组件\结果审核报告\`（可用 `--output-dir` 或 `settings.json` 的 `output.dir` 调整）。

网页上传的原文会留存到 `结果审核报告/uploads/`（`任务ID_文件名`），用于投标书生成与失败重跑；项目级导出的 zip 也落在这一目录。

Markdown 审核报告包含九节：概览、审核提示与处理建议、逐字段值与证据、规则/模型冲突对比、校验与归一化、结构化条款（采购标的、付款、交付、质保等）、模板回填、审计过程（模型调用、回喂接受/丢弃原因、清洗统计）、审核说明。

```jsonc
{
  "source":  { "path": "...", "filename": "...", "sha256": "...", "title": "..." },
  "meta":    { "use_llm": true, "llm_available": true,
               "llm_provider": "ollama", "model": "qwen2.5:3b-instruct",
               "extraction_skipped": false,
               "confidence_summary": { "...": "..." },
               "template_coverage": { "total": 46, "filled": 13, "rate": 0.2826 } },
  "document_type": { "is_tender": true, "score": 12.5, "threshold": 4.0,
                     "confidence": 0.98, "reason": "识别为招标/采购需求类文件…" },
  "template_values": { "项目名称": "...", "预算金额": "1,366,800.0000元", "...": "..." },
  "missing_templates": ["采购代理机构", "..."],
  "fields": {
    "project_name": {
      "label": "项目名称", "template": "项目名称",
      "value": "2025年社区管理与服务专业教学资源库委托服务项目",
      "raw_value": "...", "confidence": 0.91,
      "status": "ok",              // ok | low_confidence | inferred | missing | invalid | conflict
      "source": "label",           // label | table | regex | computed | inferred | llm | rule+llm
      "evidence": [{ "block_id": "B12", "quote": "...", "section": "一、项目概况" }],
      "errors": [], "warnings": [], "attempts": 0, "notes": ""
    }
  },
  "structured": {
    "project_table": {}, "business_requirements": {},
    "procurement_items": [], "procurement_total_declared": "...",
    "qualifications": "...", "after_sales": "...",
    "payment": { "installments": [], "prepay_amount": "...", "balance_amount": "...", "prepay_ratio": 0.3073 },
    "delivery": {}, "star_clauses": [], "project_summary": "...", "outline": []
  },
  "validation": { "ok": true, "errors": [], "warnings": [], "normalizations": [] },
  "cleaning":   { "paragraphs": 0, "tables": 0, "chars_raw": 0, "chars_clean": 0, "normalizations": [] },
  "review":     [{ "priority": "high|medium|low", "key": "...", "issue": "...", "value": "...", "evidence": [] }],
  "audit":      { "llm": {}, "repair_rounds": [], "conflicts": {}, "rule_candidates": 0, "evidence_blocks": 0 }
}
```

当 `meta.extraction_skipped=true` 时，说明文档类型校验未通过：`fields`、`template_values`、`structured` 为空，`meta.reject_reason` 和 `document_type` 给出原因、得分与命中特征，`review` 中有一条高优先级「文档类型」提示。此时不会调用模型，也不会执行规则抽取。

平台接入建议：

1. 先检查 `meta.extraction_skipped`：为 `true` 时不要回填模板，直接把 `reject_reason` 提示给用户。
2. 用 `template_values` 绑定模板占位符；缺失项走人工补录。
3. `review` 中 `priority=high` 的项必须人工确认后才能提交。
4. 保存 `source.sha256`、`meta.prompt_version`、`meta.model` 和 `audit`，满足审计追溯。
5. 金额字段按字符串保存，避免浮点误差；预算金额固定保留 4 位小数（如 `1,366,800.0000元`），其他金额默认 2 位；结构化的 `payment.prepay_ratio` 为 0–1 小数。

## 4. Python 集成

```python
from tender_extractor import extract_document

result = extract_document(
    "需求报告.docx",
    use_llm=True,
    settings_override={"llm": {"model": "qwen2.5:3b-instruct"}},
)
data = result.to_dict()
template_values = data["template_values"]
high_risk = [item for item in data["review"] if item["priority"] == "high"]
```

走 API 时只改 `settings_override`，代码不用动：

```python
from tender_extractor import extract_document

result = extract_document(
    "需求报告.docx",
    settings_override={
        "llm": {
            "provider": "openai",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key_env": "TENDER_EXTRACTOR_API_KEY",  # 或直接传 api_key
        }
    },
)
```

长驻服务中建议复用 `Extractor` 实例，避免重复读取配置：

```python
from tender_extractor import Extractor

extractor = Extractor()  # 配置只加载一次
result = extractor.extract(path, use_llm=True)
```

## 5. 数据清洗与校验回喂

1. **文本层**：去控制字符、统一空白、保留中文标点；表格单元格内换行压平；NFKC 仅用于匹配，不改变输出。
2. **值层**：`1,366,800.00元` / `136.68万元` / 中文大写金额统一为金额；预算金额保留 4 位小数（`1,366,800.0000元`），其他金额默认 2 位；比例统一 0–1；电话去空格；枚举先判否定再判肯定。
3. **校验层**：字段类型/必填/枚举/范围，加跨字段勾稽（标的合计 vs 预算、数量×单价 vs 金额、预付款+尾款 vs 预算、预付比例一致性）。
4. **回喂层**：把字段当前值、校验错误、允许值和原文片段拼成修复提示，最多 `max_repair_rounds` 轮；修复值必须通过同样的校验且能回链原文，否则丢弃并保留原值。
5. **模型证据核验**：模型返回的 `evidence` 必须先去掉它抄进来的 `[B12|章节]` 前缀；值必须真的出现在证据片段中。短值（如 `1`、`允许`）还要求同一片段里出现字段别名，防止“到处都能匹配”的假证据。证据不可靠时，用“值 + 别名”在原文重新定位；仍定位不到就丢弃该模型值。

在 2025 年社区管理与服务专业教学资源库样本上（Qwen2.5-3B-Instruct，CPU/单卡约 1 分钟）：
规则层 14 个字段有值、模板覆盖 13/46；模型补缺后仍为 14 个字段，但 8 个字段得到规则+模型一致确认，0 个校验错误，4 个中优先级复核项（采购人推断、采购人地址推断、合同分包推断、预付比例与模型冲突）。3B 模型曾给出“招标代理服务费”“合同份数 1”等无证据值，已被证据核验丢弃。

## 6. 配置

配置已随包发布在 `src/tender_extractor/config/`：

- `fields.json`：字段定义、中文标签、模板占位符、类型、别名、所属章节、枚举值。
- `settings.json`：接入方式（`provider`）、服务地址/模型/API key 环境变量、超时、上下文预算、最低置信度、模糊回链阈值、最大修复轮数，以及文档类型校验开关（`require_tender_document`）和阈值（`document_type_threshold`，默认 4.0）；`output.dir` 为 CLI 默认输出目录（当前为 `D:/ClaudeCode/招标需求解析组件/结果审核报告`），部署到其他机器时改这一项即可。

部署时可用 `--config-dir` 或环境变量 `TENDER_EXTRACTOR_CONFIG` 指向外置配置目录，便于不同项目调整字段和别名，不必改代码。

新增字段时，在 `fields.json` 的 `fields` 中加一项，并确保 `template` 与标准模板占位符完全一致（不含花括号）。金额字段可用 `"precision": 4` 指定小数位；不写时默认 2 位，预算金额当前配置为 4 位。

## 7. 商用与合规

| 组件 | 许可证 | 说明 |
|------|--------|------|
| 本项目代码 |  Proprietary | 按招标平台商业授权使用 |
| python-docx | MIT | 允许商用 |
| pdfplumber（可选） | MIT | 允许商用 |
| Ollama | MIT | 本地推理运行时 |
| Qwen2.5-3B-Instruct | Apache-2.0 | 允许商用，需保留版权与许可声明 |

- 默认配置下（`provider=ollama`）本组件不调用任何外部云服务，模型与文档均在本机处理；只有显式切换到 `provider=openai` 时，文档片段才会发送到所配置的接口。
- 不使用 AGPL 依赖（例如 PyMuPDF）；PDF 支持选用 MIT 的 pdfplumber。
- 模型输出不作为最终事实：所有字段带置信度、状态和原文证据，高风险项进入人工复核清单。
- 建议在生产环境固定模型版本与 `prompt_version`，并对同一文档保留输入哈希和输出快照。

## 8. 已知限制

- 文档类型校验基于标题、章节、实体词和采购表格的规则打分，不是通用分类器；阈值可通过 `document_type_threshold` 调整，误判时可用 `--allow-non-tender` 强制抽取。
- 旧版 `.doc` 不支持，请先另存为 `.docx`。
- 扫描件 PDF 无文字层时无法抽取，需要先做 OCR。
- 3B 模型对长文档和复杂表格的补缺能力有限，核心字段主要依赖规则层；建议用真实样本做回归评测后再调整提示词。
- 模板占位符变更时需同步更新 `fields.json` 的 `template` 字段。
- 招标文件生成回填内置标准模板的占位符与结构化表格（含多标的清单与多附表）；采购计划编号、采购项目编号、评分办法、合同条款、代理机构等需求报告阶段不提供的信息保持「待补充」，交由代理机构在编制时补充。
- 投标书为初稿：资格响应与技术方案按抽取到的资格条款/评分点起草，证明材料是否满足仍需投标人核对原件。
- 平台适配层目前只生成单个项目包 `P1`；多标段/分包需求需要先补充标段识别规则。

## 9. 科瑞平台适配（AI 识别预览）

`platform_adapter` 把抽取结果转换成平台新建项目页「AI 识别预览」所需的草稿结构，供平台后端直接落库：

```python
from tender_extractor import recognize_to_draft, PlatformRecognitionError

try:
    draft = recognize_to_draft("需求报告.docx", use_llm=True)
except PlatformRecognitionError as exc:
    # exc.code == "NOT_TENDER_DOCUMENT" 时提示用户换文件
    print(exc.code, exc.message)
else:
    # draft 可直接作为 initialization/drafts 的 data
    print(draft["fields"], draft["packages"], draft["subjects"])
```

也可以先抽取再转换，便于复用 `Extractor` 实例：

```python
from tender_extractor import Extractor, to_platform_draft

result = Extractor().extract("需求报告.docx", use_llm=True)
draft = to_platform_draft(result)
```

输出与平台对齐的部分：

| 组件字段 | 平台 `field_code` | 平台取值 |
|---|---|---|
| `project_name` | `project_name` | 原文 |
| `purchaser` | `procurement_unit` | 原文 |
| `supervision_unit` | `supervision_unit` | 原文 |
| `budget_amount` | `budget_amount` | 纯数字两位小数，如 `1366800.00` |
| `procurement_method` | `purchase_mode` | `PUBLIC_TENDER` 等大写码 |
| `tender_org_form` | `tender_org_form` | `SELF` / `ENTRUSTED` |
| `purchaser_contact_person` | `contact_person` | 原文 |
| `purchaser_contact` | `contact_phone` | 只保留电话号码 |
| `purchaser_address` | `link_address` | 原文；交付地点明确是采购人单位（含采购人全名或机构后缀词）时回退推断并标 `inferred` |

- 采购方式只映射平台现有的 5 个码；`邀请招标`、`框架协议采购` 会写入 `warnings` 且不回填，避免猜错。
- 项目包目前固定生成一个 `P1`，采购标的挂在 `P1` 下；多标段分包仍在规划中。
- 草稿的 `version/status/draft_uuid/project_id/file_name/file_size/warnings` 与平台一致；`evidence` 为 `{quote, page_no: null, section}`。
- 适配层只负责“识别结果 → 草稿 JSON”，不负责草稿存储、历史列表、绑定和项目创建，这些仍由平台后端负责。
- 平台金额契约是两位小数纯数字；`fields.json` 里的 `precision` 只影响组件自身输出，不会改变适配层的平台格式。

## 10. 测试

```bash
python -m pytest
```

测试覆盖清洗/归一化、DOCX 合并单元格解析、规则抽取、模板映射和校验结果，默认不访问网络与模型。