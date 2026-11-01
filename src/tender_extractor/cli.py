"""命令行入口。

平台集成优先用 Python API；CLI 用于联调、批量跑样本和回归测试。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .document import read_document
from .llm_client import build_llm_client
from .pipeline import Extractor
from .report import render_markdown
from .reviewer import resolve_settings

DEFAULT_OUTPUT_DIR = Path("D:/ClaudeCode/招标需求解析组件/data/结果审核报告")


def _resolve_output_dir(args: argparse.Namespace, settings: dict) -> Path:
    """输出目录优先级：--output-dir > settings.output.dir > 组件默认目录。"""
    raw = getattr(args, "output_dir", None) or (settings.get("output") or {}).get("dir")
    return Path(raw) if raw else DEFAULT_OUTPUT_DIR


def _resolve_output_path(raw: str | None, output_dir: Path) -> Path | None:
    """显式路径只写文件名时放进输出目录；带目录的路径按原样使用。"""
    if not raw:
        return None
    if "/" in raw or "\\" in raw:
        return Path(raw)
    path = Path(raw)
    return path if path.is_absolute() else output_dir / path.name


def _unique_path(path: Path) -> Path:
    """自动命名的输出不覆盖已有文件：重名时追加 -2、-3。"""
    if not path.exists():
        return path
    for idx in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{idx}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"输出文件重名过多：{path}")


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _build_overrides(args: argparse.Namespace) -> dict:
    settings: dict = {"llm": {}}
    if getattr(args, "provider", None):
        settings["llm"]["provider"] = args.provider
    if getattr(args, "model", None):
        settings["llm"]["model"] = args.model
    if getattr(args, "base_url", None):
        settings["llm"]["base_url"] = args.base_url
    if getattr(args, "api_key", None):
        settings["llm"]["api_key"] = args.api_key
    if getattr(args, "api_key_env", None):
        settings["llm"]["api_key_env"] = args.api_key_env
    if getattr(args, "max_repair_rounds", None) is not None:
        settings["llm"]["max_repair_rounds"] = args.max_repair_rounds
    if getattr(args, "no_llm", False):
        settings["llm"]["enabled"] = False

    review: dict = {}
    for attr, key in (
        ("review_provider", "provider"),
        ("review_model", "model"),
        ("review_base_url", "base_url"),
        ("review_api_key", "api_key"),
        ("review_api_key_env", "api_key_env"),
        ("review_scope", "scope"),
    ):
        value = getattr(args, attr, None)
        if value:
            review[key] = value
    if getattr(args, "no_review", False):
        review["enabled"] = False
    elif getattr(args, "review", False) or review:
        # 显式传了复核模型参数，视为要启用复核。
        review["enabled"] = True
    if review:
        settings["review"] = review
    return settings


def cmd_extract(args: argparse.Namespace) -> int:
    extractor = Extractor(
        config_dir=args.config_dir,
        settings_override=_build_overrides(args),
    )
    result = extractor.extract(
        args.input,
        use_llm=not args.no_llm,
        allow_non_tender=args.allow_non_tender,
    )
    data = result.to_dict()

    output_dir = _resolve_output_dir(args, extractor.settings)
    stem = Path(args.input).stem
    output_path = _resolve_output_path(args.output, output_dir)
    if output_path is None:
        output_path = _unique_path(output_dir / f"{stem}-结果.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_path = _resolve_output_path(args.md, output_dir)
    if md_path is None and not args.no_md:
        md_path = _unique_path(output_dir / f"{stem}-审核报告.md")
    if md_path:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(result), encoding="utf-8")

    if data["meta"].get("extraction_skipped"):
        reason = data["meta"].get("reject_reason", "")
        print(f"[tender-extractor] 已跳过抽取：{reason}", file=sys.stderr)
        print(
            "[tender-extractor] 如确认该文件仍需强制抽取，请加 --allow-non-tender",
            file=sys.stderr,
        )
        print(f"[tender-extractor] JSON: {output_path}", file=sys.stderr)
        if md_path:
            print(f"[tender-extractor] Markdown: {md_path}", file=sys.stderr)
        return 3

    summary = data["meta"].get("confidence_summary", {})
    coverage = data["meta"].get("template_coverage", {})
    high = sum(1 for item in data.get("review", []) if item.get("priority") == "high")
    medium = sum(1 for item in data.get("review", []) if item.get("priority") == "medium")
    if data["meta"].get("llm_available"):
        llm_state = f"{data['meta'].get('llm_provider')}/{data['meta'].get('model')}"
    else:
        llm_state = "未使用"
    print(
        f"[tender-extractor] 字段 {summary.get('fields_with_value', 0)} 个有值，"
        f"模板覆盖 {coverage.get('filled', 0)}/{coverage.get('total', 0)}，"
        f"审核提示 高{high}/中{medium}，模型 {llm_state}",
        file=sys.stderr,
    )
    print(f"[tender-extractor] JSON: {output_path}", file=sys.stderr)
    if md_path:
        print(f"[tender-extractor] Markdown: {md_path}", file=sys.stderr)

    if args.fail_on_review and high:
        return 2
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    doc = read_document(args.input)
    payload = {
        "path": doc.path,
        "title": doc.title,
        "blocks": len(doc.blocks),
        "tables": len(doc.tables),
        "cleaning": doc.cleaning,
        "outline": doc.outline,
        "sections": [
            {"section": block.section, "kind": block.kind, "preview": block.text[:120]}
            for block in doc.blocks[:30]
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    try:
        extractor = Extractor(
            config_dir=args.config_dir,
            settings_override=_build_overrides(args),
        )
        client = build_llm_client(extractor.settings.get("llm", {}))
        health = client.health()
    except Exception as exc:
        print(
            json.dumps({"available": False, "reason": str(exc)}, ensure_ascii=False, indent=2)
        )
        return 1
    payload = {
        "provider": client.provider,
        "base_url": getattr(client, "base_url", ""),
        "available": health.available,
        "reason": health.reason,
        "model": client.model,
        "model_present": health.model_present,
        "models": health.models,
    }
    ok = bool(health.available and health.model_present)

    if getattr(args, "review", False):
        review_settings = resolve_settings(
            extractor.settings.get("review") or {},
            extractor.settings.get("llm") or {},
        )
        try:
            reviewer = build_llm_client(review_settings)
            state = reviewer.health()
        except Exception as exc:
            payload["reviewer"] = {"available": False, "reason": str(exc)}
            ok = False
        else:
            payload["reviewer"] = {
                "provider": reviewer.provider,
                "base_url": getattr(reviewer, "base_url", ""),
                "model": reviewer.model,
                "available": state.available,
                "model_present": state.model_present,
                "reason": state.reason,
            }
            ok = ok and bool(state.available and state.model_present)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tender-extract",
        description="招标需求报告关键信息抽取（本地 Ollama，输出 JSON，不回填 Word）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser("extract", help="抽取需求报告")
    extract.add_argument("input", help="需求报告路径（.docx/.pdf/.txt）")
    extract.add_argument(
        "--output-dir",
        help="结果 JSON 和审核报告的默认输出目录；不填则用 settings.json 的 output.dir",
    )
    extract.add_argument(
        "-o",
        "--output",
        help="指定 JSON 路径；不填则写入输出目录，文件名为「输入名-结果.json」",
    )
    extract.add_argument(
        "--md",
        help="指定审核报告路径；不填则写入输出目录，文件名为「输入名-审核报告.md」",
    )
    extract.add_argument("--no-md", action="store_true", help="不生成审核报告 Markdown")
    extract.add_argument("--no-llm", action="store_true", help="只用规则，不调用本地模型")
    extract.add_argument(
        "--provider",
        help="模型接入方式：ollama（默认，本地）或 openai（兼容 /v1/chat/completions 的 API）",
    )
    extract.add_argument("--model", help="模型名，例如 qwen2.5:3b-instruct 或 deepseek-chat")
    extract.add_argument("--base-url", help="服务地址，默认 http://127.0.0.1:11434（ollama）")
    extract.add_argument("--api-key", help="API key；建议改用环境变量 TENDER_EXTRACTOR_API_KEY")
    extract.add_argument("--api-key-env", help="读取 API key 的环境变量名")
    extract.add_argument("--config-dir", help="配置目录（fields.json/settings.json）")
    extract.add_argument("--max-repair-rounds", type=int, help="校验回喂最大轮数")
    extract.add_argument(
        "--allow-non-tender",
        action="store_true",
        help="跳过文档类型检查，强制抽取（默认非招标/采购文件会提示并退出）",
    )
    extract.add_argument(
        "--review",
        action="store_true",
        help="启用第三方模型复核（读取 settings.json 的 review 配置）",
    )
    extract.add_argument("--no-review", action="store_true", help="关闭第三方模型复核")
    extract.add_argument("--review-provider", help="复核模型接入方式：openai（默认）或 ollama")
    extract.add_argument("--review-model", help="复核模型名，例如 deepseek-chat")
    extract.add_argument("--review-base-url", help="复核服务地址（含版本路径）")
    extract.add_argument("--review-api-key", help="复核服务 API key；建议用环境变量")
    extract.add_argument("--review-api-key-env", help="读取复核 API key 的环境变量名")
    extract.add_argument(
        "--review-scope",
        choices=["llm", "all"],
        help="复核范围：llm=只复核模型产出（默认），all=所有有值字段",
    )
    extract.add_argument("--fail-on-review", action="store_true", help="有高风险审核项时返回退出码 2")
    extract.set_defaults(func=cmd_extract)

    inspect = sub.add_parser("inspect", help="只看解析后的章节/表格结构")
    inspect.add_argument("input")
    inspect.set_defaults(func=cmd_inspect)

    health = sub.add_parser("health", help="检查模型服务（Ollama 或 API）是否可用")
    health.add_argument("--provider", help="ollama（默认）或 openai")
    health.add_argument("--base-url", help="服务地址，默认读取 settings.json")
    health.add_argument("--model", help="模型名，默认读取 settings.json")
    health.add_argument("--api-key", help="API key；建议改用环境变量")
    health.add_argument("--api-key-env", help="读取 API key 的环境变量名")
    health.add_argument("--config-dir", help="配置目录（fields.json/settings.json）")
    health.add_argument("--review", action="store_true", help="同时检查第三方复核模型（review 段）")
    health.set_defaults(func=cmd_health)

    return parser


def main(argv: list[str] | None = None) -> int:
    _reconfigure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except Exception as exc:
        print(f"[tender-extractor] 失败：{exc}", file=sys.stderr)
        if getattr(args, "debug", False):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
