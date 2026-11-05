"""模板库：内置模板 + 用户上传模板的注册、体检与取用。

为什么用户模板放 ``data/templates`` 而不是仓库 ``templates/``：仓库的
templates/ 要进 git，放用户上传的模板会把真实项目与单位信息写进提交历史；
``data/`` 已被 .gitignore 覆盖，是本地私有区。

体检口径（决定生成时会出现多少「待补充」）：
- ``unmapped``：模板里有、fields.json 没定义字段的占位符 → 需人工绑定；
- ``missing_fields``：fields.json 声明了、模板里却没有 → 模板与字段集不匹配。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .doc_generator import DEFAULT_TEMPLATE, DERIVED_KEYS
from .paths import app_data_dir
from .template_tools import (
    compile_template,
    compiled_placeholder_names,
    field_template_map,
    source_placeholder_names,
)

#: 内置模板目录：随包分发的 compiled 模板
BUILTIN_DIR = DEFAULT_TEMPLATE.parent

CATEGORIES = {"goods", "service", "works", "other"}
METHODS = {"open", "invite", "negotiation", "inquiry", "competitive", "single", "other"}

#: 内置模板没有 meta.json，不给类型标签的话「按所选类型推荐模板」永远匹配不上。
#: 键用文件名而非 stem，避免两个同名不同后缀的文件被归成一类。
BUILTIN_PROFILES: dict[str, tuple[str, str]] = {
    "广东省政府采购公开招标标准模板.docx": ("service", "open"),
    "广东省政府采购公开招标标准模板（货物类）.docx": ("goods", "open"),
    "广东省政府采购竞争性磋商标准模板（服务类）.docx": ("service", "negotiation"),
}


@dataclass
class TemplateInfo:
    """一套模板的元信息（不含绝对路径，避免经接口外泄本机路径）。"""

    id: str
    name: str
    builtin: bool = False
    category: str = "other"
    method: str = "other"
    version: str = "1.0"
    source_filename: str = ""
    created_at: str = ""
    placeholders: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    is_default: bool = False

    def to_meta(self) -> dict[str, Any]:
        """落盘用的 meta.json 内容（默认状态单独存，不进 meta）。"""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "method": self.method,
            "version": self.version,
            "source_filename": self.source_filename,
            "created_at": self.created_at,
            "placeholders": list(self.placeholders),
            "unmapped": list(self.unmapped),
            "missing_fields": list(self.missing_fields),
        }

    def public(self) -> dict[str, Any]:
        """接口返回体。"""
        data = self.to_meta()
        data["builtin"] = self.builtin
        data["placeholder_count"] = len(self.placeholders)
        data["is_default"] = self.is_default
        return data


class TemplateLibrary:
    """模板库门面。内置模板只读，用户模板可增删改。"""

    def __init__(
        self,
        store_dir: Path | str | None = None,
        builtin_dir: Path | str | None = None,
    ) -> None:
        self.store_dir = Path(store_dir) if store_dir else (app_data_dir() / "templates")
        self.builtin_dir = Path(builtin_dir) if builtin_dir else BUILTIN_DIR

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def list(self) -> list[TemplateInfo]:
        """内置在前、用户在后；两者都标注是否默认。"""
        infos = self._builtin_infos() + self._user_infos()
        default = self.effective_default()
        for info in infos:
            info.is_default = info.id == default
        return infos

    def get(self, template_id: str) -> TemplateInfo | None:
        template_id = (template_id or "").strip()
        if not template_id:
            return None
        if template_id.startswith("builtin-"):
            info = self._find_builtin(template_id)
        else:
            folder = self.store_dir / template_id
            meta_file = folder / "meta.json"
            info = self._read_meta(folder) if meta_file.is_file() else None
        if info is not None:
            info.is_default = info.id == self.effective_default()
        return info

    def compiled_path(self, template_id: str | None) -> Path:
        """模板对应的 compiled .docx。空 id 或失效 id 一律回退到默认模板。"""
        candidates = [template_id or "", self.effective_default() or ""]
        for item in candidates:
            if not item:
                continue
            path = self._compiled_for(item)
            if path is not None and path.is_file():
                return path
        if DEFAULT_TEMPLATE.is_file():
            return DEFAULT_TEMPLATE
        raise FileNotFoundError("没有任何可用模板：请先运行 scripts/prepare_template.py")

    def effective_default(self) -> str | None:
        """用户设置的默认模板；失效时回退到内置标准模板。

        只在文件名层面判断，不解析 docx——这个方法在每次列表、生成、预览
        里都会被调用，解析三份模板的占位符会让接口平白多花上百毫秒。
        """
        configured = self._configured_default()
        if configured and self._compiled_for(configured) is not None:
            return configured
        if not self.builtin_dir.is_dir():
            return None
        paths = sorted(path for path in self.builtin_dir.glob("*.docx") if path.is_file())
        if not paths:
            return None
        preferred = next((path for path in paths if "标准模板" in path.stem), paths[0])
        return self._builtin_id(preferred.stem)

    # ------------------------------------------------------------------
    # 写入（仅用户模板）
    # ------------------------------------------------------------------

    def create(
        self,
        filename: str,
        payload: bytes,
        *,
        name: str = "",
        category: str = "other",
        method: str = "other",
        version: str = "1.0",
    ) -> TemplateInfo:
        """上传一套模板：落盘 → 体检占位符 → 转 compiled → 写 meta。"""
        filename = str(filename or "").strip()
        if not filename:
            raise ValueError("缺少模板文件名")
        if not filename.lower().endswith(".docx"):
            raise ValueError("只支持 .docx 模板；.doc/.pdf 请先用 Word 另存为 .docx")
        if not payload:
            raise ValueError("模板内容为空")

        template_id = self._new_id()
        folder = self.store_dir / template_id
        folder.mkdir(parents=True, exist_ok=True)
        source = folder / "source.docx"
        source.write_bytes(payload)

        try:
            names = source_placeholder_names(source)
        except Exception as exc:  # noqa: BLE001 - 损坏文件统一按不可解析处理
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError(f"无法解析该 .docx，请确认文件未损坏（{exc}）") from exc

        if not names:
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError(
                "模板里没有找到 {占位符}，请确认上传的是招标文件模板"
                "（占位符写法为 {项目名称} 这样的单花括号）"
            )

        try:
            compile_template(source, folder / "compiled.docx")
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(folder, ignore_errors=True)
            raise ValueError(f"模板占位符转换失败：{exc}") from exc

        info = self._build_info(
            template_id=template_id,
            name=name.strip() or Path(filename).stem,
            placeholders=sorted(names),
            category=category,
            method=method,
            version=version,
            source_filename=filename,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._write_meta(folder, info)
        info.is_default = info.id == self.effective_default()
        return info

    def rename(self, template_id: str, name: str) -> TemplateInfo:
        info = self.get(template_id)
        if info is None:
            raise KeyError("模板不存在")
        if info.builtin:
            raise ValueError("内置模板不能重命名")
        name = str(name or "").strip()
        if not name:
            raise ValueError("模板名称不能为空")
        if len(name) > 80:
            raise ValueError("模板名称不能超过 80 个字")
        folder = self.store_dir / info.id
        info.name = name
        self._write_meta(folder, info)
        return info

    def set_default(self, template_id: str) -> TemplateInfo:
        info = self.get(template_id)
        if info is None:
            raise KeyError("模板不存在")
        self.store_dir.mkdir(parents=True, exist_ok=True)
        (self.store_dir / "default.json").write_text(
            json.dumps({"id": info.id}, ensure_ascii=False), encoding="utf-8"
        )
        info.is_default = True
        return info

    def delete(self, template_id: str) -> None:
        info = self.get(template_id)
        if info is None:
            raise KeyError("模板不存在")
        if info.builtin:
            raise ValueError("内置模板不能删除")
        shutil.rmtree(self.store_dir / info.id, ignore_errors=True)
        if self._configured_default() == info.id:
            default_file = self.store_dir / "default.json"
            default_file.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # 内部：内置模板
    # ------------------------------------------------------------------

    def _builtin_infos(self) -> list[TemplateInfo]:
        if not self.builtin_dir.is_dir():
            return []
        infos: list[TemplateInfo] = []
        for path in sorted(self.builtin_dir.glob("*.docx")):
            if not path.is_file():
                continue
            try:
                names = sorted(compiled_placeholder_names(path))
            except Exception:  # noqa: BLE001 - 单文件损坏不该拖垮整个列表
                names = []
            category, method = BUILTIN_PROFILES.get(path.name, ("other", "other"))
            infos.append(
                self._build_info(
                    template_id=self._builtin_id(path.stem),
                    name=path.stem,
                    placeholders=names,
                    builtin=True,
                    category=category,
                    method=method,
                    created_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(
                        timespec="seconds"
                    ),
                    source_filename=path.name,
                )
            )
        return infos

    def _find_builtin(self, template_id: str) -> TemplateInfo | None:
        for info in self._builtin_infos():
            if info.id == template_id:
                return info
        return None

    @staticmethod
    def _builtin_id(stem: str) -> str:
        digest = hashlib.sha1(stem.encode("utf-8")).hexdigest()[:8]
        return f"builtin-{digest}"

    # ------------------------------------------------------------------
    # 内部：用户模板
    # ------------------------------------------------------------------

    def _user_infos(self) -> list[TemplateInfo]:
        if not self.store_dir.is_dir():
            return []
        infos: list[TemplateInfo] = []
        for folder in sorted(self.store_dir.iterdir()):
            if not folder.is_dir() or not (folder / "meta.json").is_file():
                continue
            info = self._read_meta(folder)
            if info is not None:
                infos.append(info)
        infos.sort(key=lambda item: item.created_at, reverse=True)
        return infos

    def _read_meta(self, folder: Path) -> TemplateInfo | None:
        try:
            data = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return TemplateInfo(
            id=str(data.get("id") or folder.name),
            name=str(data.get("name") or folder.name),
            builtin=False,
            category=str(data.get("category") or "other"),
            method=str(data.get("method") or "other"),
            version=str(data.get("version") or "1.0"),
            source_filename=str(data.get("source_filename") or ""),
            created_at=str(data.get("created_at") or ""),
            placeholders=[str(x) for x in data.get("placeholders") or []],
            unmapped=[str(x) for x in data.get("unmapped") or []],
            missing_fields=[str(x) for x in data.get("missing_fields") or []],
        )

    def _write_meta(self, folder: Path, info: TemplateInfo) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "meta.json").write_text(
            json.dumps(info.to_meta(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _configured_default(self) -> str | None:
        try:
            data = json.loads((self.store_dir / "default.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(data, dict):
            value = str(data.get("id") or "").strip()
            return value or None
        return None

    def _compiled_for(self, template_id: str) -> Path | None:
        if template_id.startswith("builtin-"):
            if not self.builtin_dir.is_dir():
                return None
            for path in self.builtin_dir.glob("*.docx"):
                if self._builtin_id(path.stem) == template_id:
                    return path
            return None
        if not template_id or "/" in template_id or "\\" in template_id:
            return None
        path = self.store_dir / template_id / "compiled.docx"
        return path if path.is_file() else None

    def _new_id(self) -> str:
        return f"tpl-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"

    @staticmethod
    def _build_info(
        template_id: str,
        name: str,
        placeholders: list[str],
        *,
        builtin: bool = False,
        category: str = "other",
        method: str = "other",
        version: str = "1.0",
        source_filename: str = "",
        created_at: str = "",
    ) -> TemplateInfo:
        defined = set(field_template_map())
        named = set(placeholders)
        # 派生占位符（如「采购需求」）由生成器从其他字段推导，不属于「未匹配」，
        # 否则前端会提示用户去绑定一个本来就不该手工填的位置。
        derived = set(DERIVED_KEYS)
        return TemplateInfo(
            id=template_id,
            name=name,
            builtin=builtin,
            category=category if category in CATEGORIES else "other",
            method=method if method in METHODS else "other",
            version=version,
            source_filename=source_filename,
            created_at=created_at,
            placeholders=placeholders,
            unmapped=sorted(named - defined - derived),
            missing_fields=sorted(defined - named),
        )
