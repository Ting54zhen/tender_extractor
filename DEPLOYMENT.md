# 部署清单

> 目标：一台新机器，从零到能用。所有命令在项目根目录执行。
> **Windows 与 macOS 都支持**：Windows 走 `start.bat`，macOS 走 `python3 scripts/launch.py`，下文把两套命令并列写出。

---

## 一、装软件（3 个 + 1 个可选）

| 装什么 | Windows | macOS | 说明 |
|---|---|---|---|
| Python 3.10+ | python.org 安装包，勾选 Add to PATH | `brew install python@3.12` | 装完 `python -V` 确认 |
| Ollama | https://ollama.com/download | `brew install ollama` | 本地模型服务，必须常驻 |
| Git | https://git-scm.com/ | `xcode-select --install` | 拉代码用 |
| （可选）LibreOffice | https://www.libreoffice.org/ | `brew install --cask libreoffice` | 只有「导出 PDF」才需要 |

---

## 二、拿代码 + 装依赖

```bat
git clone <仓库地址> 招标需求解析组件
cd 招标需求解析组件

:: Windows
start.bat setup --dev

:: macOS
python3 scripts/launch.py setup --dev
```

`setup --dev` 会自动建 `.venv` 并装依赖。需要 PDF 抽取时加 `--pdf`。

> 依赖只有 4 个：`python-docx`、`docxtpl`、`pdfplumber`（PDF 抽取）、`pytest`（测试）。
> **以后所有 python 命令都要用虚拟环境里的解释器**：Windows 是 `.venv\Scripts\python.exe`，macOS 是 `.venv/bin/python`。
> 用系统 `python` 会报 `ModuleNotFoundError`。

---

## 三、装模型（2 个）

```bat
ollama serve
ollama pull qwen2.5:7b-instruct-q4_K_M
ollama pull qwen2.5:1.5b-instruct
```

- `qwen2.5:7b-instruct-q4_K_M` = 抽取模型（主）
- `qwen2.5:1.5b-instruct` = 复核模型

`ollama serve` 要一直开着。显存/内存不够 → 看下面「五、配置」第 2 条换 3B。

---

## 四、启动 + 自检

```bat
:: Windows
.\.venv\Scripts\python.exe -m tender_extractor health
.\.venv\Scripts\python.exe -m tender_extractor health --review

:: macOS
.venv/bin/python -m tender_extractor health
.venv/bin/python -m tender_extractor health --review
```

两条都显示 `"available": true` 就说明模型通了。然后启动服务：

```bat
:: Windows
start.bat web

:: macOS
python3 scripts/launch.py web
```

打开 `http://127.0.0.1:8765`。端口被占时：Windows `start.bat web --port 9000`，macOS `python3 scripts/launch.py web --port 9000`。

---

## 五、配置（只有 1 个必改，其余按需）

### 1. 必改：输出目录

打开网页 → 左下「设置」→ 「输出」→ 把输出目录改成这台机器的路径，保存。

> 仓库自带的 `settings.json` 里写死了一个绝对路径（`D:/ClaudeCode/...`），
> 换机器不改的话，生成的文件会写到不存在的位置。

### 2. 显存不够：换小模型

设置 → 模型接入：抽取模型改成 `qwen2.5:3b-instruct`，再 `ollama pull qwen2.5:3b-instruct`。

### 3. 改用在线 API（不想起 Ollama）

```bat
set TENDER_EXTRACTOR_API_KEY=sk-xxx
set TENDER_REVIEWER_API_KEY=sk-yyy
```

设置 → 模型接入：provider 改成 `openai`，填 base_url（带版本路径，如 `https://api.deepseek.com/v1`）和模型名。

> key 只放环境变量，别写进 `settings.json` 提交。

---

## 六、验证跑通

1. 首页右上角状态徽章是正常的（不是红色）。
2. 拖入 `samples/` 里一份报告 → 完整链路 → 发送。
3. 等「完成」：字段表有值、每条有原文证据、能进「填写工作台」。
4. 填写台右上「生成招标文件」→ `data\结果审核报告\` 下出现 `.docx`。

---

## 七、跑测试

```bat
:: Windows
.\.venv\Scripts\python.exe -m pytest tests -q

:: macOS
.venv/bin/python -m pytest tests -q
```

99 项，全绿为准。Windows 上偶尔会出现 1 个 `WinError 10053` 连接中断，单跑那条用例复核即可，不是代码问题。

---

## 八、打包成 exe 给别人（不装 Python 的同事）

```bat
.\.venv\Scripts\python.exe scripts\build_exe.py
.\.venv\Scripts\python.exe scripts\check_exe.py
```

产物：`dist\招标需求解析.exe`。macOS 上产物是 `dist/招标需求解析`（没有 .exe），**只能在本平台运行，不能互相拷贝**。

- exe 放进**空文件夹**运行，会在旁边自动建 `data\`（别放 `C:\Program Files` 这种没写权限的目录）。
- exe 里的 `settings.json` 改不了，参数一律用**设置页**改。
- 首次运行可能被杀软拦一下，加白即可。

---

## 九、GitHub 协作（共同开发用这个）

仓库是 SSH 地址（`git remote -v` 可查）。日常开发一人一个分支，走 PR 合并，不直接推 `main`。

### 9.1 首次：配 SSH + 拉代码

```bat
:: 生成密钥（一路回车即可）
ssh-keygen -t ed25519 -C "你的邮箱"

:: 打印公钥，整段复制
type %USERPROFILE%\.ssh\id_ed25519.pub
```

把公钥贴到 GitHub → Settings → SSH and GPG keys → New SSH key，然后：

```bat
git clone git@github.com:<你的组织>/<仓库名>.git 招标需求解析组件
cd 招标需求解析组件

:: Windows
start.bat setup --dev

:: macOS
python3 scripts/launch.py setup --dev
```

不想配 SSH 就用 HTTPS（每次推送输 token）：

```bat
git clone https://github.com/<你的组织>/<仓库名>.git 招标需求解析组件
```

### 9.2 日常开发流程

```bat
:: 1. 开工前先同步
git checkout main
git pull

:: 2. 开一个自己的分支
git checkout -b feat/字段校验修复

:: 3. 改代码、跑测试（见第七节）

:: 4. 提交
git add .
git commit -m "fix(validation): 修复多号码电话校验失败"

:: 5. 推到自己的分支
git push -u origin feat/字段校验修复
```

然后到 GitHub 网页发 **Pull Request**，@ 一个同事 review，通过后点 Merge，再删掉这个分支。

### 9.3 提交信息规范（沿用仓库现有风格）

```
feat(模块): 新功能说明
fix(模块): 修了什么 bug
refactor(模块): 结构调整
docs: 文档
chore: 杂项（依赖、脚本、配置）
test: 测试
```

一行写清「动了哪 + 为什么」，不要写「update」「改了点东西」。

### 9.4 合并后同步到本地

```bat
git checkout main
git pull
```

### 9.5 绝对不要提交的东西

`.venv/`、`data/`、`dist/`、`build/`、`参赛材料/` 已在 `.gitignore` 里排除。
提交前 `git status --short` 扫一眼，出现 `data/`、`dist/` 或带个人路径的 `settings.json` 就说明要停手检查。

---

## 常见报错速查

| 报错 / 现象 | 怎么办 |
|---|---|
| `ModuleNotFoundError: docxtpl` | 用了系统 python，改用 `.venv\Scripts\python.exe` |
| `WinError 10048` 端口占用 | `start.bat web --port 9000` |
| 状态徽章红、模型不可用 | `ollama serve` + `ollama list` 核对模型名 |
| 抽取很慢 | 换 3B 模型，或改 API |
| 输出目录写不进去 | 设置页改成本机路径 |
| 导出 PDF 报「没有可用转换器」 | 装 LibreOffice，或 `.venv\Scripts\pip install pywin32` 且有 Word |
| 打包后页面 404 | `frontend/` 没打进包，重打包并跑 `check_exe.py` |
| macOS 上提示 `start.bat: command not found` | macOS 没有 bat 脚本 | 改用 `python3 scripts/launch.py <子命令>` |
| macOS 导出 PDF 失败 | 未装 LibreOffice | `brew install --cask libreoffice`，代码会自动找应用包内的 soffice |
