"""分析抽取结果的缺失字段明细（只读）。

用法：
    python scripts/analyze_missing.py [结果json路径]

不传路径时自动取本地最新一份 `*-结果.json`；不在代码里写死任何样本路径。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESULT_DIRS = (
    PROJECT_ROOT / "data" / "结果审核报告",
    PROJECT_ROOT / "data" / "归档" / "结果审核报告",
)


def latest_result() -> Path | None:
    """在本地输出目录里找最新的结果 JSON，兼容已归档的历史输出。"""
    candidates: list[Path] = []
    for folder in RESULT_DIRS:
        if folder.is_dir():
            candidates += list(folder.glob("*-结果.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.stat().st_mtime)


def main() -> int:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = latest_result()
        if target is None:
            print("未找到结果 JSON，请显式传入路径：python scripts/analyze_missing.py <结果.json>")
            return 1
    if not target.exists():
        print(f"NOT_FOUND: {target}")
        return 1
    data = json.loads(target.read_text(encoding="utf-8"))
    fields = data.get("fields") or {}

    template_fields = {
        key: value for key, value in fields.items() if value.get("template")
    }
    missing = {
        key: value
        for key, value in template_fields.items()
        if value.get("value") in (None, "")
    }
    print(f"模板字段: {len(template_fields)}  有值: {len(template_fields) - len(missing)}  缺失: {len(missing)}")
    print(f"meta.model: {(data.get('meta') or {}).get('model', '')}")
    print(f"llm_available: {(data.get('meta') or {}).get('llm_available')}")
    print("\n=== 缺失明细 ===")
    for key, value in missing.items():
        notes = (value.get("notes") or "").replace("\n", " ")[:60]
        print(
            f"- {value.get('label')} ({key}) "
            f"status={value.get('status')} source={value.get('source')} "
            f"attempts={value.get('attempts')}"
        )
        if value.get("errors"):
            print(f"    errors: {'；'.join(value['errors'])[:110]}")
        if notes:
            print(f"    notes: {notes}")

    print("\n=== 状态分布 ===")
    counts: dict[str, int] = {}
    for value in fields.values():
        counts[value.get("status", "?")] = counts.get(value.get("status", "?"), 0) + 1
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
