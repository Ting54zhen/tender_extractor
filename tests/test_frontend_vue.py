"""验证构建产物的本机分发、旧 URL 和源码隔离；交互另由真实浏览器覆盖。"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from collections.abc import Iterator

import pytest

from tender_extractor import web
from tender_extractor.storage import Database


class _Assets(HTMLParser):
    """读取真实 HTML 标签，避免依赖构建器属性顺序或散列文件名。"""

    def __init__(self) -> None:
        super().__init__()
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"script", "link"}:
            return
        values = dict(attrs)
        url = values.get("src" if tag == "script" else "href")
        if url and not url.startswith("data:"):
            self.urls.add(url)


@contextmanager
def _server(tmp_path: Path) -> Iterator[str]:
    """仅启动回环 HTTP 服务，不触发模型、外部请求或构建过程。"""
    service = web.Service(output_dir=tmp_path / "out", db=Database(tmp_path / "tender.db"))
    httpd = web.make_server("127.0.0.1", 0, service)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_vue_pages_and_assets_are_served_locally(tmp_path: Path) -> None:
    """七页别名与查询参数继续有效，实际 JS/CSS 和许可由 Python 提供。"""
    with _server(tmp_path) as base:
        assets: set[str] = set()
        for name in ("index", "fill", "templates", "projects", "settings", "company", "compare"):
            aliases = ("/", "/index.html") if name == "index" else (f"/{name}", f"/{name}.html")
            for route in aliases:
                with urllib.request.urlopen(base + route + "?job=local-test", timeout=5) as response:
                    html = response.read().decode("utf-8")
                    assert response.status == 200
                assert 'id="vue-root"' in html
                parser = _Assets()
                parser.feed(html)
                assert any(url.endswith(".js") for url in parser.urls)
                assert any(url.endswith(".css") for url in parser.urls)
                assert all(url.startswith("/assets/") for url in parser.urls)
                assets.update(parser.urls)
        for route in sorted(assets):
            with urllib.request.urlopen(base + route, timeout=5) as response:
                assert response.status == 200
                expected = "text/javascript" if route.endswith(".js") else "text/css"
                assert response.headers.get_content_type() == expected
                assert response.read()
        with urllib.request.urlopen(base + "/assets/VUE-LICENSE.txt", timeout=5) as response:
            assert response.headers.get_content_type() == "text/plain"
            assert b"MIT License" in response.read()
        for route in ("/src/pages/fill/FillPage.vue", "/node_modules/vue/package.json", "/package-lock.json"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + route, timeout=5)
            assert error.value.code == 404


def test_frontend_directory_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """源码缺构建时也不能退回源目录；冻结程序始终使用包内产物。"""
    monkeypatch.delenv("TENDER_FRONTEND_DIR", raising=False)
    monkeypatch.setattr(web, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(web.sys, "frozen", False, raising=False)
    (tmp_path / "frontend").mkdir()
    assert web._resolve_frontend_dir() == tmp_path / "frontend" / "dist"
    monkeypatch.setattr(web.sys, "frozen", True)
    assert web._resolve_frontend_dir() == web.BASE_DIR / "static"
    monkeypatch.setenv("TENDER_FRONTEND_DIR", str(tmp_path / "custom-dist"))
    assert web._resolve_frontend_dir() == tmp_path / "custom-dist"


def test_missing_build_and_sibling_traversal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """缺构建给明确提示，目录前缀相同也不能读取 dist 的兄弟目录文件。"""
    built = tmp_path / "dist"
    built.mkdir()
    sibling = tmp_path / "dist-secret"
    sibling.mkdir()
    (sibling / "secret.js").write_text("private", encoding="utf-8")
    monkeypatch.setattr(web, "STATIC_DIR", built)
    with _server(tmp_path) as base:
        for route, status in (("/", 503), ("/assets/missing.js", 404), ("/assets/../../dist-secret/secret.js", 403)):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(base + route, timeout=5)
            assert error.value.code == status
            if status == 503:
                assert "npm run build" in json.loads(error.value.read())["error"]
