# AGENTS.md · AI 协作规则

任何 AI 会话进入本仓库，先读这一份。这里只写**能被检查**的约束；
目录职责、接口契约、协作流程的完整说明在 [DEVELOPMENT.md](DEVELOPMENT.md)，不在这里重复。

> 生效方式：根目录 `AGENTS.md` 会被全文注入上下文，前提是**不存在** `CODEBUDDY.md`。
> **不要新建 `CODEBUDDY.md`**，它会静默屏蔽本文件。规则在会话开始时加载，改完要开新会话才生效。

## 1. 项目是什么

输入采购需求报告（.docx / .pdf / .txt），输出三样东西：**结构化字段 JSON**、**人工审核报告**、**可编辑的招标文件 .docx**。

产品主线是 **AI 招标文件制作**。投标侧模块（`bid_extract.py`、`compliance_matcher.py`、`bid_writer.py`、`company_profile.py`）处于**缓行**状态：保留、不删、不迭代，除非用户明确要求。

## 2. 三条硬约束

违反任何一条，改动即为不合格，无论功能是否跑通。

**① 本地闭环优先**
默认 `provider=ollama`，文档不出本机；Web 服务默认只监听 `127.0.0.1`。
判定：新增代码里出现向外部域名发请求，且不是用户显式开启的 `provider=openai` 路径 → 违规。

**② Web 资源本机分发**
后端只用标准库 `http.server`；前端使用 Vue 3 多页面，生产版资源随仓库与 exe 本地分发，保留 MIT 许可证；无 CDN、无外部运行资源引用；用户已授权仅在开发机使用 npm + Vite 构建。
判定：新增 Python 运行时依赖、HTML 引用外部 `<script>` / `<link>`，或前端需联网加载运行资源 → 违规（需用户事先同意）。用户已明确授权 Vue + npm + Vite，此例外不扩展到其他框架或大型组件库。

**③ 证据可回链**
任何由模型产出的值，必须带 `block_id` + 原文 `quote`，且值真的出现在引文里，否则丢弃。
判定：新增字段没有 `evidence`，或为了“填充率好看”而放宽证据核验 → 违规。

## 3. 技术栈

| 项 | 内容 |
|---|---|
| 语言 | Python 3.10+（当前 3.12） |
| 运行时依赖 | `python-docx`、`docxtpl`；PDF 可选 `pdfplumber` |
| 模型 | 本地 Ollama（默认）或 OpenAI 兼容接口 |
| Web | 标准库 `http.server`，无框架 |
| 前端 | Vue 3.5.45 + Vite 8.3.6（MIT），npm 管理，多页面、沿用 CSS |
| 测试 | pytest |

**禁止引入**：Flask / FastAPI / Django、React / 其他前端框架或未经授权的打包器、Vue Router / Pinia / 大型组件库、PyMuPDF（AGPL）、任何新 Python 运行时依赖——除非用户明确要求并确认许可证。前端只发布 `frontend/dist/` 构建产物，Node/npm 仅开发和打包时使用；锁定版本、保留锁文件与 MIT 许可。

## 4. 目录边界

| 目录 | 改之前要知道 |
|---|---|
| `src/tender_extractor/` | 核心引擎。改它就必须跑全量测试 |
| `frontend/` | 界面。Vite 开发模式热更新；Python 模式需 npm run build 后刷新，无需重启 |
| `tests/` | 改动 `src/` 必须同步；禁止为了过测试而删断言 |
| `templates/` | `compiled/` 是 `prepare_template.py` 生成的，**不要手改** |
| `data/` | 真实采购文件与本机数据，**严禁提交**（已在 `.gitignore`） |
| `samples/` | 共享样本，必须脱敏后才能进 |
| `docs/` | GitHub Pages 站点，对外公开，不放内部文档 |
| `参赛材料/` | 含个人信息，已忽略，不提交 |

## 5. 常用命令

```bash
python -m pytest tests -q                          # 全量测试（必须全绿）
python -m tender_extractor extract "文件.docx"      # 完整链路抽取
python -m tender_extractor extract "文件.docx" --no-llm   # 仅规则，不依赖模型
python -m tender_extractor generate "文件.docx"     # 生成招标文件 .docx
npm --prefix frontend ci                          # 首次安装前端锁定依赖
npm --prefix frontend run dev                     # Vite 开发，127.0.0.1:5173
npm --prefix frontend run build                   # 构建本机前端资源
python -m tender_extractor.web                     # 本地网页，127.0.0.1:8765
python scripts/prepare_template.py                 # 源模板 → compiled/
python scripts/build_exe.py                        # 打包 exe（产物 dist/）
python scripts/check_exe.py                        # 打包后验收
```

Windows 下也可用 `start.bat <子命令>`。输出默认落在 `data/结果审核报告/`。

## 6. 编码约定

按现有代码的写法，不发明新风格：

- 文件开头 `from __future__ import annotations`，全量类型注解，用内置泛型（`list[str]`、`dict[str, Any]`、`str | None`）。
- 跨模块数据结构用 `dataclass`（见 `models.py`），不用裸 dict 传递。
- 模块和函数用**中文 docstring 说明“为什么这样设计”**，不复述代码字面；私有辅助函数前缀 `_`。
- **金额一律走 `Decimal`**（`cleaning.py` 的 `parse_money` / `format_money` / `quantize_money`），禁止 float 参与金额运算。
- **不静默兜底**：规则与模型冲突时两个候选都保留并标 `conflict`；原文没有就标 `missing`，不编造、不用默认值冒充抽取结果。
- 不写只对当前样本成立的硬编码（具体单位名、具体金额、具体项目名）。

## 7. 测试要求

- 改动 `src/` 后必须 `python -m pytest tests -q` **全绿**才能交付；红灯不许说“已完成”。
- 新增行为要配测试；修 bug 先写能复现的用例。
- 测试**不得访问网络、不得依赖 Ollama 或任何模型服务**；模型链路用 mock。
- 断言失败时改代码，不改断言来迁就代码。
- 报告结果只能说实际跑出来的：跑了贴命令和结果，没跑就明说没跑。

## 8. 数据与安全

- `data/`、`参赛材料/` 不进 git；提交前 `git status --short` 自查。
- `samples/` 里的单位名改「某单位」，人名、电话、地址打码，但**保留章节结构与表格形态**。
- 模型输出不是事实：所有值带置信度、状态、证据，高风险项进人工复核清单。不要在任何文档里把模型输出说成“已确认”。
- 不把真实采购文件内容、密钥、内网地址贴进代码、测试、示例或提交信息。

## 9. 禁止清单

- 不新增 Python 运行时依赖，不引入后端 Web 框架；Vue + npm + Vite 是已授权的前端例外，不增加 Router / Pinia / 大型组件库。
- 不改 API 契约（`/api/*` 路径或字段结构）而不同步 `frontend/README.md`。
- 不伪造证据、不在测试里写死样本答案来“通过”。
- 不删除 `.codebuddy/`、`tests/`、`samples/`、`templates/`。
- 不提交 `data/`、`dist/`、`build/`、`.venv/`。
- 不在 `src/` 留调试 `print`。

## 10. 计划（每次更新后必须同步）

当前进度与下一步优先级只有一个来源：**`DEVELOPMENT.md` 第十一节**「当前状态与下一步」。
接手任务前先读它；不要在别处另起一份计划。

**每次更新后自动同步「下一步」章节**：本仓库任何改动（代码、测试、文档、配置、打包）交付前，无需用户提醒，主动更新 `DEVELOPMENT.md` 第十一节，重点是「下一步（优先级从高到低）」清单：

- 本次完成的事项从「下一步」移入「已完成」，补上结果（如测试项数变化、新增能力）；
- 本次暴露的新待办加入「下一步」，写清启动条件 / 依赖，按优先级重排序号；
- 只改这一节，不在别处（含本文件）另写一份进度清单。

判定：交付时 `DEVELOPMENT.md` 第十一节未反映本次改动 → 视为未完成，不算交付。
