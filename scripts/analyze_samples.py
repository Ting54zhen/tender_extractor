"""从 data/采购需求样本 的真实文件提取结构，用于校准各类型字段口径。

为什么需要它：字段集不能靠「猜某类文件要什么」来定，得看真实文件里
到底出现了哪些章节、哪些表格、哪些栏目。但真实文件含单位名与项目信息，
所以脚本只输出**结构**（标题、表头、栏目名），不输出正文段落，
结果写在 data/ 下（已忽略），不进 git。

用法：
    python scripts/analyze_samples.py            # 汇总打印
    python scripts/analyze_samples.py --json     # 同时落盘 data/样本结构分析/
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import docx

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "data" / "采购需求样本"
OUT_DIR = ROOT / "data" / "样本结构分析"

#: 章节标题：第X章 / 第X部分，或中文序号「一、」
CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百零0-9]+[章部分]")
SECTION_RE = re.compile(r"^[一二三四五六七八九十]+、")
#: 栏目名：形如「采购人：」「预算金额：」，只取冒号前的短词
LABEL_RE = re.compile(r"^([\u4e00-\u9fa5（）()A-Za-z0-9]{2,24})[：:]")
#: 业务类型：先判工程（「维护维修」属工程，「运行维护服务」属服务），再判服务与货物。
#: 只用项目名称，避免正文里「采购」二字把所有文件都判成货物。
CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("工程", ("工程", "修缮", "改造", "施工", "安装", "维修")),
    ("服务", ("服务", "运维", "维护", "配送", "安保", "物业", "培训", "咨询")),
    ("货物", ("采购", "购置", "设备", "材料", "图书", "家具", "车辆", "耗材")),
)


def iter_paragraphs(document) -> list[Any]:
    """正文 + 表格 + 页眉页脚，按 XML 元素去重（合并单元格会重复枚举）。"""
    seen: set[int] = set()
    result: list[Any] = []

    def add(para) -> None:
        key = id(para._p)
        if key not in seen:
            seen.add(key)
            result.append(para)

    for para in document.paragraphs:
        add(para)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for para in cell.paragraphs:
                    add(para)
    for section in document.sections:
        for container in (section.header, section.footer):
            for para in container.paragraphs:
                add(para)
    return result


def classify(texts: list[str], filename: str) -> tuple[str, str]:
    """返回（文件种类，业务类型）。种类看第一章标题，类型看项目名称用词。"""
    head = "".join(texts[:80])
    if "磋商邀请" in head or "磋商文件" in head:
        kind = "磋商文件"
    elif "谈判邀请" in head:
        kind = "谈判文件"
    elif "询价通知书" in head:
        kind = "询价文件"
    elif "投标邀请" in head or "投标人须知" in head:
        kind = "招标文件"
    else:
        kind = "需求报告"
    # 「工程技术职业学院」「工程学院」是机构名，不代表项目是工程类
    project = filename.replace("工程技术", "").replace("工程学院", "")
    for name, words in CATEGORY_WORDS:
        if any(word in project for word in words):
            return kind, name
    return kind, "未识别"


def analyze(path: Path) -> dict[str, Any]:
    document = docx.Document(str(path))
    paragraphs = iter_paragraphs(document)
    texts = [para.text.strip() for para in paragraphs if para.text.strip()]

    chapters: list[str] = []
    labels: list[str] = []
    for text in texts:
        if CHAPTER_RE.match(text) or SECTION_RE.match(text):
            chapters.append(text[:40])
        match = LABEL_RE.match(text)
        if match:
            labels.append(match.group(1).strip())

    headers: list[list[str]] = []
    for table in document.tables:
        if not table.rows:
            continue
        row = [cell.text.strip().replace("\n", " ") for cell in table.rows[0].cells]
        row = [cell[:24] for cell in row if cell]
        if row:
            headers.append(row)

    title = texts[0][:60] if texts else path.stem
    kind, category = classify(texts, path.name)
    return {
        "file": path.name,
        "title": title,
        "kind": kind,
        "category": category,
        "paragraphs": len(texts),
        "tables": len(document.tables),
        "star_clauses": sum(text.count("★") for text in texts),
        "chapters": chapters[:40],
        "demand_sections": collect_demand_sections(texts),
        "headers": headers[:40],
        "labels": sorted(set(labels)),
    }


#: 判断字段口径的关键词：只统计出现次数，不输出原句。
KEYWORDS = (
    "最后报价", "磋商轮次", "工程量清单", "最高投标限价", "招标控制价",
    "安全生产许可证", "项目经理", "缺陷责任期", "质量保修", "验收办法",
    "价格扣除", "核心产品", "是否允许进口产品", "权重", "售后服务",
    "培训要求", "人员要求", "服务范围", "付款方式", "结算方式",
    "品牌", "产地", "型号",
)


def read_texts(path: Path) -> list[str]:
    document = docx.Document(str(path))
    return [para.text.strip() for para in iter_paragraphs(document) if para.text.strip()]


def collect_demand_sections(texts: list[str]) -> list[str]:
    """只取「第二章 采购需求」内部的二级栏目，用来判断该类型要填哪些栏目。"""
    inside = False
    out: list[str] = []
    for text in texts:
        if CHAPTER_RE.match(text):
            inside = "采购需求" in text
            continue
        if inside and SECTION_RE.match(text):
            out.append(re.sub(r"\s+", " ", text)[:30])
    return out


def classify_header(header: list[str]) -> str:
    """把表格按用途分组：品目表 / 标的清单 / 技术参数表 / 其他。"""
    joined = "".join(header)
    if "参数性质" in joined:
        return "技术参数表"
    if "分项预算单价" in joined or "分项预算总价" in joined:
        return "标的清单"
    if "品目号" in joined and "采购标的" in joined:
        return "品目表"
    return "其他表"


def summarize(results: list[dict[str, Any]]) -> None:
    """按「采购方式 × 业务类型」聚合栏目与表头列，输出可直接对照字段的结论。"""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in results:
        key = (item["kind"], item["category"])
        grouped.setdefault(key, []).append(item)

    for (kind, category), items in sorted(grouped.items()):
        print(f"\n=== {kind} · {category} · {len(items)} 份 ===")
        sections: Counter[str] = Counter()
        columns: dict[str, Counter[str]] = {}
        for item in items:
            sections.update(item["demand_sections"])
            for header in item["headers"]:
                counter = columns.setdefault(classify_header(header), Counter())
                counter.update(set(header))
        print("  采购需求栏目：" + "；".join(f"{k}×{v}" for k, v in sections.most_common(24)))
        for table_name in ("品目表", "标的清单", "技术参数表", "其他表"):
            counter = columns.get(table_name)
            if counter:
                print(f"  {table_name}列：" + " / ".join(name for name, _ in counter.most_common(20)))


def main(argv: list[str]) -> int:
    if not SAMPLE_DIR.is_dir():
        print(f"NOT_FOUND: {SAMPLE_DIR}")
        return 1
    files = sorted(path for path in SAMPLE_DIR.glob("*.docx") if path.is_file())
    results = [analyze(path) for path in files]

    if "--grep" in argv:
        for path in files:
            texts = read_texts(path)
            hits = {word: sum(text.count(word) for text in texts) for word in KEYWORDS}
            hits = {word: count for word, count in hits.items() if count}
            if hits:
                print(f"{path.name[:44]}：" + "、".join(f"{w}×{c}" for w, c in hits.items()))
        return 0

    for item in results:
        print(f"[{item['kind']} · {item['category']}] {item['file'][:48]}"
              f"（表 {item['tables']} · ★{item['star_clauses']}）")

    summarize(results)

    if "--json" in argv:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        target = OUT_DIR / f"samples-{datetime.now():%Y%m%d-%H%M%S}.json"
        target.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
