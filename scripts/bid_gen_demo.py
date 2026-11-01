import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from tender_extractor.bid_writer import generate_bid_document

use_llm = "--llm" in sys.argv
tender = (
    Path("data") / "采购需求样本"
    / "飞书20261028-2113162026-2027年广州天河体育中心安保服务采购招标文件（2026051103）.docx"
)
profile = Path("src/tender_extractor/config/company_profile.example.json")

start = time.perf_counter()
result = generate_bid_document(tender, profile, use_llm=use_llm)
cost = time.perf_counter() - start

print("use_llm=%s | cost=%.0fs" % (use_llm, cost))
print("summary:", result.summary)
if result.meta:
    print("meta:", result.meta)

print("\n--- 缺口清单 (%d) ---" % len(result.gaps))
for gap in result.gaps:
    print("  - %s | 需要：%s" % (gap["requirement"][:50], gap["need"]))

out = Path("data") / "结果审核报告" / ("投标书初稿-7b.md" if use_llm else "投标书初稿-快速.md")
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(result.markdown, encoding="utf-8")
print("\nsaved: %s | chars=%d" % (out, len(result.markdown)))
print("\n--- 前 900 字 ---")
print(result.markdown[:900])
print("\nBID_GEN_DONE")
