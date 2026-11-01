# frontend · 前端模块

本目录是项目的**前端模块**，与 Python 后端（`src/tender_extractor/`）完全解耦。
前端成员只需修改本目录，无需了解 Python。

## 文件说明

| 文件 | 页面 | 功能 |
|------|------|------|
| `index.html` | 主页面 | 上传采购需求报告，查看结构化字段、复核清单、模板映射与审核报告 |
| `compare.html` | 比对页面 | L2 双文档比对：上传招标文件 + 投标文件，查看符合性比对结果 |

技术形态：纯 HTML/CSS/JS 单文件（无 CDN、无构建工具），直接改源码即可生效。

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

响应字段结构以 `src/tender_extractor/web.py` 的 `Handler` 实现为准。

## 修改规范

1. 不要改动 API 路径与请求方式；渲染逻辑随便改；
2. 保持「无 CDN、无外部依赖」约束（文档不出内网的设计要求）；
3. 提交前用 `start.bat web` 自测一遍上传与比对流程；
4. 涉及新字段展示时，先确认后端 JSON 里确实有该字段。
