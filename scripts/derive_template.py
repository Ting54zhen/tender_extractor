"""从标准招标模板派生不同品目 / 采购方式的模板变体。

为什么用派生而不是重写：现有模板的 46 个占位符已与 fields.json 一一映射，
8 张附表（商务要求 / 标的清单 / 技术参数）的回填逻辑也按它的表格结构实现。
重写一份会把这两条已验证的链路推倒；派生只改差异条款，未改动的部分与
已验证的模板结构完全一致，出问题也容易逐段对比回滚。

替换规则按「长词优先、单遍替换」执行：
- 长词优先：先匹配「投标人」再匹配「投标」，否则会得到「响应人」；
- 单遍替换：替换结果不再参与匹配，避免「投标 → 响应」被二次改写。

产物是派生草稿，条款表述需业务/法务复核后才能对外使用。

用法：
    python scripts/derive_template.py            # 生成全部变体
    python scripts/derive_template.py goods      # 只生成某一种
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE = PROJECT_ROOT / "templates" / "广东省政府采购公开招标标准模板.docx"
OUT_DIR = PROJECT_ROOT / "templates"
PLACEHOLDER = re.compile(r"(?<!\{)\{([^{}]{1,30})\}(?!\})")
#: 带捕获组的切分用正则：拆开后奇数下标是占位符，替换时必须整体跳过，
#: 否则「{中标供应商数量}」会被术语替换改成「{成交供应商数量}」而导致字段失配
PLACEHOLDER_SPLIT = re.compile(r"(\{[^{}]{1,30}\})")

#: 货物类公开招标：政府模板正文偏服务表述，货物类只需改合同章与中小企业口径
GOODS: dict[str, object] = {
    "output": "广东省政府采购公开招标标准模板（货物类）.docx",
    "exact": {
        "公开招标文件": "公开招标文件（货物类）",
    },
    "replace": {
        "3.进口产品": "3.产品要求",
        "全部服务的承接方应为中小微企业、监狱企业或残疾人福利性单位": (
            "全部货物应由中小微企业、监狱企业或残疾人福利性单位制造"
        ),
        "二、服务范围": "二、供货范围",
        "四、服务期间（项目完成期限）": "四、交货期（项目完成期限）",
        "1.委托服务期间自": "1.交货期自",
        "乙方服务满一个月视为具备本项目实施条件": "乙方供货满一个月视为具备本项目实施条件",
        "乙方提供的服务不符合本合同规定的": "乙方提供的货物不符合本合同规定的",
        "乙方未能按本合同规定的交货时间提供服务": "乙方未能按本合同规定的交货时间交付货物",
        "甲方无正当理由拒收接受服务": "甲方无正当理由拒收货物",
        "到期拒付服务款项的": "到期拒付货物款项的",
        "十六、技术和服务要求响应表": "十六、技术要求和商务要求响应表",
        "《技术和服务要求响应表》": "《技术要求和商务要求响应表》",
        "项目实施方案、质量保证及售后服务承诺等": "项目实施方案、质量保证、售后服务及培训方案等",
    },
    # (锚点原文, 追加在锚点之后的段落)
    "insert_after": [
        (
            "若本项目不允许采购进口产品，如供应商所投产品为进口产品，其响应将被认定为响应无效。",
            [
                "3.1 核心产品：本项目核心产品详见“采购需求”章。提供相同品牌核心产品的不同投标人"
                "参加同一合同项下投标的，按一家投标人计算。",
            ],
        ),
    ],
    # 生成后不应再出现的旧术语（只检查确属错误的，正常保留的词不列）
    "forbidden": ["全部服务的承接方"],
}

#: 竞争性磋商（服务类）：采购方式不同，全文术语需从「招标/投标」切换到「磋商/响应」
NEGOTIATION: dict[str, object] = {
    "output": "广东省政府采购竞争性磋商标准模板（服务类）.docx",
    "exact": {
        "公开招标文件": "竞争性磋商文件",
        "第一章投标邀请": "第一章 磋商邀请",
        "第三章 投标人须知": "第三章 磋商须知",
        "四.提交投标文件截止时间、开标时间和地点：": "四.提交响应文件截止时间、开启时间和地点：",
        "提交投标文件截止时间和开标时间：详见招标公告及其变更公告（如有）": (
            "提交响应文件截止时间和开启时间：详见磋商公告及其变更公告（如有）"
        ),
    },
    "replace": {
        "采用公开招标方式": "采用竞争性磋商方式",
        "投标保证金": "磋商保证金",
        "中标通知书": "成交通知书",
        "评标委员会": "磋商小组",
        "招标文件": "磋商文件",
        "投标文件": "响应文件",
        "招标公告": "磋商公告",
        "投标人": "供应商",
        "开标": "开启",
        "评标": "评审",
        "中标": "成交",
        "招标": "磋商",
        "投标": "响应",
    },
    "insert_before": [
        (
            "第四章 评标",
            [
                "磋商程序",
                "1.磋商小组由采购人代表和评审专家共3人以上单数组成，其中评审专家人数不得少于"
                "磋商小组成员总数的三分之二。",
                "2.磋商小组对响应文件进行资格性审查和符合性审查，通过审查的供应商方可参加磋商。",
                "3.磋商小组所有成员集中与单一供应商分别进行磋商。磋商中，磋商的任何一方不得透露"
                "与磋商有关的其他供应商的技术资料、价格和其他信息。",
                "4.磋商结束后，磋商小组要求所有实质性响应的供应商在规定时间内提交最后报价，"
                "最后报价是响应文件的有效组成部分。",
                "5.磋商小组按照最后报价由低到高或者综合评分由高到低的顺序推荐3家以上成交候选供应商。",
            ],
        ),
    ],
    "forbidden": ["投标人", "招标文件", "投标文件", "评标", "中标通知"],
}

VARIANTS: dict[str, dict[str, object]] = {
    "goods": GOODS,
    "negotiation": NEGOTIATION,
}


def iter_paragraphs(document) -> list[Paragraph]:
    """正文、表格、页眉页脚的段落，按 XML 元素去重（合并单元格会被重复枚举）。"""
    result: list[Paragraph] = []
    seen: set[int] = set()

    def add(para: Paragraph) -> None:
        key = id(para._p)
        if key in seen:
            return
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


def build_pattern(mapping: dict[str, str]) -> re.Pattern[str]:
    """把替换表编译成单遍正则，长词优先，避免链式替换出错。"""
    keys = sorted(mapping, key=len, reverse=True)
    return re.compile("|".join(re.escape(key) for key in keys))


def sub_outside_placeholders(text: str, regex: re.Pattern[str], mapping: dict[str, str]) -> str:
    """只替换占位符之外的文本，保住 {字段名} 原样。"""
    parts = PLACEHOLDER_SPLIT.split(text)
    for index in range(0, len(parts), 2):
        parts[index] = regex.sub(lambda m: mapping[m.group(0)], parts[index])
    return "".join(parts)


def rewrite_paragraph(para: Paragraph, transform) -> bool:
    """按 transform 改写单个段落，返回是否改动。

    优先逐 run 改写以保留段内混排格式；只有当目标词被 Word 拆到多个 run
    （模板里很常见）时才合并重写，只保留首个 run 的格式。
    """
    original = para.text
    if not original:
        return False
    expected = transform(original)
    if expected == original:
        return False

    per_run = [transform(run.text) for run in para.runs]
    if per_run and "".join(per_run) == expected:
        for run, text in zip(para.runs, per_run):
            run.text = text
        return True
    if not para.runs:
        return False
    para.runs[0].text = expected
    for run in para.runs[1:]:
        run.text = ""
    return True


def normalize_placeholders(paragraphs: list[Paragraph]) -> int:
    """把 `{ 字段名 }` 归一成 `{字段名}`。

    源模板里「{ 中标候选供应商推荐家数}」带前导空格，与 fields.json 的
    字段名对不上，生成时该位置永远显示待补充，这里一并修掉。
    """

    def transform(text: str) -> str:
        return PLACEHOLDER.sub(lambda m: "{" + m.group(1).strip() + "}", text)

    return sum(1 for para in paragraphs if rewrite_paragraph(para, transform))


def clone_paragraph(source: Paragraph, text: str) -> Paragraph:
    """复制段落的格式（pPr + 首个 run 的 rPr），换成新文本。"""
    new_p = copy.deepcopy(source._p)
    runs = new_p.findall(qn("w:r"))
    keep = runs[0] if runs else None
    for child in list(new_p):
        if child.tag == qn("w:pPr"):
            continue
        if keep is not None and child is keep:
            for sub in list(keep):
                if sub.tag != qn("w:rPr"):
                    keep.remove(sub)
            continue
        new_p.remove(child)

    if keep is None:
        keep = OxmlElement("w:r")
        new_p.append(keep)
    run = OxmlElement("w:t")
    run.text = text
    run.set(qn("xml:space"), "preserve")
    keep.append(run)
    return Paragraph(new_p, source._parent)


def insert_lines(
    anchor: Paragraph, lines: list[str], style_source: Paragraph, before: bool = False
) -> int:
    """在锚点段落之前/之后插入若干段，格式继承 style_source。"""
    cursor = anchor
    for index, line in enumerate(lines):
        new_para = clone_paragraph(style_source, line)
        if before and index == 0:
            cursor._p.addprevious(new_para._p)
            cursor = new_para
        elif before:
            cursor._p.addnext(new_para._p)
            cursor = new_para
        else:
            anchor._p.addnext(new_para._p)
            anchor = new_para
    return len(lines)


def find_paragraph(paragraphs: list[Paragraph], text: str) -> Paragraph | None:
    for para in paragraphs:
        if para.text.strip() == text.strip():
            return para
    return None


def pick_body_style(paragraphs: list[Paragraph]) -> Paragraph:
    """挑一个普通正文段落作格式模板：有 run、不以数字编号之外的形式开头。"""
    for para in paragraphs:
        stripped = para.text.strip()
        if len(stripped) > 40 and para.runs and not stripped.startswith(("第", "一", "二", "三")):
            return para
    return paragraphs[0]


def collect_placeholders(paragraphs: list[Paragraph]) -> list[str]:
    names: list[str] = []
    for para in paragraphs:
        names.extend(PLACEHOLDER.findall(para.text))
    return names


def derive(name: str, config: dict[str, object]) -> int:
    if not SOURCE.exists():
        print(f"NOT_FOUND: {SOURCE}")
        return 1

    document = docx.Document(str(SOURCE))
    paragraphs = iter_paragraphs(document)
    style_source = pick_body_style(paragraphs)
    normalized = normalize_placeholders(paragraphs)
    before_placeholders = collect_placeholders(paragraphs)
    inserted = 0

    # 先插入再替换：锚点用原文，插入的新文本不会撞上旧术语
    for anchor_text, lines in config.get("insert_after", []) or []:
        anchor = find_paragraph(paragraphs, anchor_text)
        if anchor is None:
            print(f"  ! 未找到插入锚点（after）：{anchor_text[:40]}")
            continue
        inserted += insert_lines(anchor, list(lines), style_source)
    for anchor_text, lines in config.get("insert_before", []) or []:
        anchor = find_paragraph(paragraphs, anchor_text)
        if anchor is None:
            print(f"  ! 未找到插入锚点（before）：{anchor_text[:40]}")
            continue
        inserted += insert_lines(anchor, list(lines), style_source, before=True)

    # 锚点插入后段落对象仍有效，重新枚举以纳入新增段落
    paragraphs = iter_paragraphs(document)
    changed = 0
    exact: dict[str, str] = config.get("exact", {})  # type: ignore[assignment]
    for para in paragraphs:
        stripped = para.text.strip()
        if stripped in exact and para.runs:
            para.runs[0].text = exact[stripped]
            for run in para.runs[1:]:
                run.text = ""
            changed += 1

    mapping: dict[str, str] = config.get("replace", {})  # type: ignore[assignment]
    regex = build_pattern(mapping)

    def transform(text: str) -> str:
        return sub_outside_placeholders(text, regex, mapping)

    for para in paragraphs:
        if rewrite_paragraph(para, transform):
            changed += 1

    target = OUT_DIR / str(config["output"])
    document.save(str(target))

    after_paragraphs = iter_paragraphs(docx.Document(str(target)))
    after_placeholders = collect_placeholders(after_paragraphs)
    forbidden = {
        word: sum(1 for p in after_paragraphs if word in p.text)
        for word in config.get("forbidden", []) or []
    }

    print(f"[{name}] -> {target.name}")
    print(f"  占位符归一化 {normalized} 段，按段改写 {changed} 处，插入 {inserted} 段")
    print(
        f"  占位符 {len(before_placeholders)} → {len(after_placeholders)}"
        f"（去重 {len(set(before_placeholders))} → {len(set(after_placeholders))}）"
    )
    if set(before_placeholders) != set(after_placeholders):
        lost = set(before_placeholders) - set(after_placeholders)
        print(f"  ! 占位符集合变化，丢失：{sorted(lost)}")
    for word, count in forbidden.items():
        if count:
            print(f"  ! 仍残留「{word}」{count} 段")
    return 0


def fix_source() -> int:
    """就地修源模板的占位符空格（{ 字段名} → {字段名}），幂等。"""
    if not SOURCE.exists():
        print(f"NOT_FOUND: {SOURCE}")
        return 1
    document = docx.Document(str(SOURCE))
    changed = normalize_placeholders(iter_paragraphs(document))
    if changed:
        document.save(str(SOURCE))
    print(f"[fix-source] {SOURCE.name} 归一化 {changed} 段")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--fix-source" in args:
        return fix_source()
    targets = args or list(VARIANTS)
    unknown = [item for item in targets if item not in VARIANTS]
    if unknown:
        print(f"未知变体：{unknown}，可选：{list(VARIANTS)}")
        return 2
    for name in targets:
        code = derive(name, VARIANTS[name])
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
