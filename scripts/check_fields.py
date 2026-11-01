"""快速打印指定字段的抽取结果（只读诊断工具）。

用法：
    python scripts/check_fields.py [字段key ...]
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from tender_extractor import extract_document  # noqa: E402

DOC = (
    PROJECT_ROOT
    / "data"
    / "采购需求样本"
    / "飞书20261028-2113162026年安全服务项目（保安、宿管）招标文件（2026051103）.docx"
)
DEFAULT_KEYS = [
    "agency_contact",
    "agency_project_contact",
    "agency_project_phone",
    "inquiry_contact",
    "inquiry_phone",
]


def main() -> int:
    keys = sys.argv[1:] or DEFAULT_KEYS
    if not DOC.exists():
        print(f"NOT_FOUND: {DOC}")
        return 1
    result = extract_document(str(DOC), use_llm=False)
    for key in keys:
        field_value = result.fields.get(key)
        if field_value is None:
            print(f"{key}: <无此字段>")
            continue
        print(
            f"{key}: value={field_value.value!r} "
            f"conf={field_value.confidence:.3f} "
            f"status={field_value.status} "
            f"source={field_value.source}"
        )
        if field_value.errors:
            print(f"    errors={field_value.errors}")
        if field_value.evidence:
            quote = field_value.evidence[0].quote.replace("\n", " ")[:80]
            print(f"    evidence={quote!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
