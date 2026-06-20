# coding=utf-8
"""全球新闻日报样例研究命令"""

from collections import Counter
from pathlib import Path
from typing import Dict

from trendradar.integrations.digest_parser import parse_digest_file
from trendradar.integrations.digest_research import format_research_report, research_digest_sample, save_research_report


def run_analyze_digest_sample(config: Dict, sample_path: str) -> bool:
    path = Path(sample_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    if not path.exists():
        print("❌ 样例文件不存在: {0}".format(path))
        print("\n获取样例的两种方式:")
        print("  1. Gmail 下载: 配置 gmail_digest 后运行")
        print("     python -m trendradar --fetch-gmail-digest --digest-analyze")
        print("  2. 手动导出: 将 MD 附件放到 output/digest/samples/ 后指定路径")
        return False

    digest_config = config.get("GMAIL_DIGEST", {})
    newsletter_id = digest_config.get("NEWSLETTER_ID", "")

    try:
        research = research_digest_sample(path)
        if path.suffix.lower() in (".md", ".markdown") and newsletter_id:
            enriched = parse_digest_file(path, newsletter_id=newsletter_id)
            research.parsed_item_count = max(research.parsed_item_count, len(enriched.items))
            research.parse_quality = enriched.parse_quality
            research.sample_items = [item.title[:120] for item in enriched.items[:8]]
            research.categories = dict(Counter(item.category or "未分类" for item in enriched.items))
            research.integration_hints = research.integration_hints  # rebuild below
            from trendradar.integrations.digest_research import _build_integration_hints
            research.integration_hints = _build_integration_hints(research)
    except Exception as exc:
        print("❌ 分析失败: {0}".format(exc))
        return False

    out_dir = Path(digest_config.get("OUTPUT_DIR", "output/digest")) / "research"
    report_path = save_research_report(research, out_dir)

    print(format_research_report(research))
    print("\n✅ 研究报告已保存: {0}".format(report_path))
    print("   JSON: {0}".format(report_path.with_suffix(".json")))
    return True


def find_local_samples(output_dir: str = "output/digest") -> list:
    """查找本地可能的样例文件"""
    root = Path(output_dir)
    patterns = ["**/*.html", "**/*.htm", "**/*.md", "**/*.pdf", "**/*.txt"]
    found = []
    for pat in patterns:
        found.extend(root.glob(pat))
    return sorted(
        [
            p for p in found
            if "research" not in p.parts
            and not p.name.endswith("_preview.md")
            and not p.name.endswith("_intro.html")
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
