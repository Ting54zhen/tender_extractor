# -*- coding: utf-8 -*-
"""exe 端到端验收：独立目录运行 dist 产物，验证页面/DB 落位。"""

import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = ROOT / "dist" / "招标需求解析.exe"
PORT = 8877


def get(url: str, timeout: float = 5.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status


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
    try:
        for i in range(45):
            time.sleep(2)
            if proc.poll() is not None:
                print(f"[check] 进程提前退出，退出码 {proc.returncode}")
                break
            try:
                status = get(f"http://127.0.0.1:{PORT}/api/health", timeout=3)
                print(f"[check] /api/health -> {status}（{2 * (i + 1)}s 后就绪）")
                ok = True
                break
            except Exception:
                continue

        if ok:
            for path in ("/projects", "/settings", "/assets/projects.js"):
                try:
                    status = get(f"http://127.0.0.1:{PORT}{path}")
                    print(f"[check] {path} -> {status}")
                except Exception as exc:
                    print(f"[check] {path} 失败：{exc}")
                    ok = False
            db = workdir / "data" / "tender.db"
            print("[check] DB 落位 exe 同目录：" + ("OK" if db.is_file() else "MISSING"))
            ok = ok and db.is_file()
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
