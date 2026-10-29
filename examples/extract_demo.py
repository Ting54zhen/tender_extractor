"""最小集成示例：输入需求报告，打印模板占位符映射和复核清单。

用法：
    python examples/extract_demo.py "需求报告.docx"
    python examples/extract_demo.py "需求报告.docx" --no-llm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tender_extractor import extract_document  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", help="需求报告 .docx/.pdf/.txt")
    parser.add_argument("--no-llm", action="store_true", help="只用规则层")
    parser.add_argument("-o", "--output", help="可选：保存完整 JSON")
    args = parser.parse_args()

    result = extract_document(args.input, use_llm=not args.no_llm)
    data = result.to_dict()

    if data["meta"].get("extraction_skipped"):
        print(f"== 未执行抽取 ==\n{data['meta'].get('reject_reason', '')}")
        return 3

    print("== 模板占位符 ==")
    print(json.dumps(data["template_values"], ensure_ascii=False, indent=2))
    print("\n== 待人工复核 ==")
    for item in data["review"]:
        print(f"[{item['priority']}] {item['label']}: {item['issue']} (当前值: {item['value']!r})")

    if args.output:
        Path(args.output).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n完整结果已写入 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
