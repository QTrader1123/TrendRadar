# coding=utf-8
"""Gmail 全球新闻日报附件下载命令"""

from pathlib import Path
from typing import Dict

from trendradar.context import AppContext
from trendradar.integrations.digest_research import research_digest_sample, save_research_report
from trendradar.integrations.digest_parser import digest_to_markdown_section, parse_digest_file
from trendradar.integrations.gmail_digest import fetch_gmail_digest, load_latest_digest, load_latest_intro


def run_fetch_gmail_digest(config: Dict, *, analyze: bool = False) -> bool:
    """
    执行 Gmail 附件下载；可选解析最新文件并输出预览。

    Returns:
        True 成功或无新邮件，False 配置/连接失败
    """
    digest_config = config.get("GMAIL_DIGEST", {})
    if not digest_config.get("ENABLED", False):
        print("❌ gmail_digest 未启用，请在 config/config.yaml 中设置 gmail_digest.enabled: true")
        return False

    ctx = AppContext(config)
    try:
        result = fetch_gmail_digest(digest_config, get_time_func=ctx.get_time)
        if not result.success:
            print(f"❌ {result.message}")
            for err in result.errors:
                print(f"   {err}")
            return False

        print(f"✅ {result.message}")
        if analyze or digest_config.get("PARSE_AFTER_FETCH", False):
            _analyze_latest(digest_config)
        return True
    finally:
        ctx.cleanup()


def _analyze_latest(digest_config: Dict) -> None:
    output_dir = digest_config.get("OUTPUT_DIR", "output/digest")
    latest = load_latest_digest(output_dir)
    if not latest:
        print("[Digest] 本地尚无已下载文件，跳过解析预览")
        return

    latest = load_latest_digest(output_dir)
    if not latest:
        print("[Digest] 本地尚无已下载文件，跳过解析预览")
        return

    newsletter_id = digest_config.get("NEWSLETTER_ID", "")
    source_name = digest_config.get("SOURCE_NAME", "全球新闻日报")
    parsed = parse_digest_file(latest, newsletter_id=newsletter_id)
    print(f"\n[Digest] 解析: {latest.name}")
    print(f"  来源: {source_name} | 格式: {parsed.format} | 质量: {parsed.parse_quality} | 条目: {len(parsed.items)}")

    intro = load_latest_intro(output_dir)
    if intro:
        print(f"  邮件简介: {intro.name}")

    if parsed.items:
        print("  前 3 条:")
        for item in parsed.items[:3]:
            cat = f"[{item.category}] " if item.category else ""
            print(f"    • {cat}{item.title[:80]}")

    research = research_digest_sample(latest)
    research_dir = Path(output_dir) / "research"
    report_path = save_research_report(research, research_dir)
    print(f"  结构研究报告: {report_path}")

    preview_path = latest.parent / f"{latest.stem}_preview.md"
    preview_path.write_text(
        digest_to_markdown_section(parsed, source_name=source_name),
        encoding="utf-8",
    )
    print(f"  预览 MD: {preview_path}")
