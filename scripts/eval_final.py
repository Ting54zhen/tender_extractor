import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))

from tender_extractor.document import read_document
from tender_extractor.llm_client import build_llm_client
from tender_extractor.pipeline import Extractor
from tender_extractor.relation_extract import _select_category_context, extract_relations

extractor = Extractor()
client = build_llm_client(extractor.settings.get("llm", {}))
path = (
    Path("data") / "采购需求样本"
    / "飞书20261028-2113162026-2027年广州天河体育中心安保服务采购招标文件（2026051103）.docx"
)
doc = read_document(path)

for kind in ("qualifications", "scoring_items", "disqualifications"):
    selected = _select_category_context(doc, kind, 3500)
    print("[%s] selected %d blocks: %s" % (kind, len(selected), [b.id for b in selected]))

start = time.perf_counter()
rel = extract_relations(client, doc, extractor.settings)
print("\ncost: %.1fs" % (time.perf_counter() - start))
print("meta:", rel.meta)

print("\n=== qualifications (%d) ===" % len(rel.qualifications))
for q in rel.qualifications:
    print("  - %s" % q.name[:110])

print("\n=== scoring_items (%d) ===" % len(rel.scoring_items))
for s in rel.scoring_items:
    print("  - %s | %s 分" % (s.name, s.value))

print("\n=== disqualifications (%d) ===" % len(rel.disqualifications))
for d in rel.disqualifications:
    print("  - %s | %s" % (d.name[:110], d.value))

print("\nEVAL_FINAL_DONE")
