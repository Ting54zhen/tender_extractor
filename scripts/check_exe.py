# -*- coding: utf-8 -*-
"""exe 端到端验收：独立目录运行 dist 产物，验证页面、DB 落位与模板库新链路。

为什么要独立目录：exe 是 onefile，页面/模板/配置可能来自打包资源，也可能
被同目录的开发源码「顺手」补齐；只有拷到空目录运行，才能证明这一个 exe
真的能独立跑起来。frozen 下的路径解析（_MEIPASS）最容易在这里暴露。
"""

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "招标需求解析.exe"
PORT = 8877
BASE = f"http://127.0.0.1:{PORT}"

#: 基础页面与静态资源；(路径, 期望状态码, 标签)
CHECKS = [
    ("/projects", 200, "项目工作台"),
    ("/settings", 200, "设置页"),
    ("/assets/projects.js", 200, "项目页脚本"),
    ("/templates", 200, "模板库页"),
    ("/fill", 200, "填写工作台"),
    ("/assets/preview.css", 200, "预览样式"),
    ("/assets/preview.js", 200, "预览渲染器"),
    ("/api/templates", 200, "模板库接口"),
]


def get(url: str, timeout: float = 5.0) -> tuple[int, bytes]:
    """返回 (status, body)；HTTPError 也算响应，便于验证 404 这类预期结果。"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001 - 连接失败统一按 (0, 空) 处理
        print(f"[check] 请求异常 {url}: {exc}")
        return 0, b""


def post_json(url: str, payload: dict, timeout: float = 5.0) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:  # noqa: BLE001
        print(f"[check] 请求异常 {url}: {exc}")
        return 0, b""


def decode(body: bytes) -> str:
    return body.decode("utf-8", "replace")


def main() -> int:
    # Windows 控制台默认 GBK：exe 输出的替换字符无法编码时不应中断验收
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    if not EXE.is_file():
        print(f"[check] 缺少 {EXE}")
        return 1
    workdir = Path(tempfile.mkdtemp(prefix="tender-exe-"))
    target = workdir / EXE.name
    shutil.copy2(EXE, target)
    print(f"[check] exe 已拷贝到 {workdir}")

    proc = subprocess.Popen(
        [str(target), "--no-browser", f"--port={PORT}"],
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="gbk",
        errors="replace",
    )
    ok = False
    results: list[tuple[str, bool]] = []
    try:
        for i in range(45):
            time.sleep(2)
            if proc.poll() is not None:
                print(f"[check] 进程提前退出，退出码 {proc.returncode}")
                break
            status, _ = get(f"{BASE}/api/health", timeout=3)
            if status == 200:
                print(f"[check] /api/health -> {status}（{2 * (i + 1)}s 后就绪）")
                ok = True
                break

        if ok:
            for path, expected, label in CHECKS:
                status, body = get(f"{BASE}{path}")
                passed = status == expected
                results.append((label, passed))
                print(f"[check] {label}: HTTP {status} {'OK' if passed else 'FAIL'}")

            # 页面内容：确认打包进去的是新版前端，而不是旧页面凑巧返回 200
            _, body = get(f"{BASE}/templates")
            text = decode(body)
            results.append((
                "模板库页含上传与占位符入口",
                "上传模板" in text and "占位符" in text,
            ))
            _, body = get(f"{BASE}/fill")
            text = decode(body)
            results.append((
                "填写页含预览与自定义字段",
                "生成前预览" in text and "自定义字段" in text,
            ))

            # 内置模板与 frozen 路径解析：列表能和占位符对上，预览才真能跑
            status, body = get(f"{BASE}/api/templates")
            templates = []
            if status == 200:
                try:
                    templates = json.loads(decode(body)).get("templates") or []
                except (ValueError, TypeError):
                    templates = []
            names = [str(item.get("name") or "") for item in templates]
            has_variants = any("货物类" in n for n in names) and any(
                "竞争性磋商" in n for n in names
            )
            results.append(("三套内置模板可读", len(templates) >= 3 and has_variants))
            print(f"[check] 内置模板：{names}")

            if templates:
                template_id = str(templates[0].get("id") or "")
                status, body = get(f"{BASE}/api/templates/{template_id}/preview")
                sections = []
                if status == 200:
                    try:
                        sections = json.loads(decode(body)).get("sections") or []
                    except (ValueError, TypeError):
                        sections = []
                results.append(("frozen 下模板预览可解析", bool(sections)))
                print(f"[check] 模板空值预览：HTTP {status}，{len(sections)} 个章节")

            status, _ = post_json(f"{BASE}/api/jobs/no-such-job/preview", {"values": {}})
            results.append(("任务预览路由已注册", status == 404))
            print(f"[check] 不存在任务的预览：HTTP {status}（期望 404）")

            db = workdir / "data" / "tender.db"
            db_ok = db.is_file()
            results.append(("DB 落位 exe 同目录", db_ok))
            print(f"[check] DB 落位 exe 同目录：{'OK' if db_ok else 'MISSING'}")

            failed = [label for label, passed in results if not passed]
            print(f"[check] 检查项：{len(results) - len(failed)}/{len(results)} 通过")
            for label in failed:
                print(f"[check]   FAIL {label}")
            ok = not failed
    finally:
        # Windows onefile 会再拉起子进程：terminate 只杀引导器，子进程仍监听
        # 端口并持有 stdout 管道，communicate() 等不到 EOF 会永久挂住。
        # 必须按进程树终止，再收尾。
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                out = ""
        if out and out.strip():
            print("[check] ---- exe 输出（截断 800 字）----")
            print(out[:800])

    shutil.rmtree(workdir, ignore_errors=True)
    print("[check] 结果：" + ("通过" if ok else "未通过"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
