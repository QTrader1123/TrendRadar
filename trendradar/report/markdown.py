# coding=utf-8
"""
Markdown 报告渲染模块

将报告数据渲染为结构化 Markdown，供本地存档与 IMA 知识库上传。
"""

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from trendradar.ai.formatter import _format_list_content
from trendradar.report.formatter import format_title_for_platform


DEFAULT_REGION_ORDER = ["hotlist", "rss", "new_items", "standalone", "ai_analysis"]

_MODE_DISPLAY = {
    "daily": "当日汇总",
    "current": "当前榜单",
    "incremental": "增量模式",
}


def _format_news_line(idx: int, title_data: Dict, *, show_source: bool = True, show_keyword: bool = False) -> str:
    formatted = format_title_for_platform(
        "wework", title_data, show_source=show_source, show_keyword=show_keyword
    )
    return f"{idx}. {formatted}"


def _render_hotlist_md(report_data: Dict, display_mode: str = "keyword") -> str:
    if not report_data.get("stats"):
        return ""

    lines = ["## 热点新闻", ""]
    for stat in report_data["stats"]:
        word = stat.get("word", "")
        count = stat.get("count", 0)
        lines.append(f"### {word} ({count} 条)")
        lines.append("")
        show_source = display_mode == "keyword"
        show_keyword = display_mode == "platform"
        for j, title_data in enumerate(stat.get("titles", []), 1):
            lines.append(_format_news_line(j, title_data, show_source=show_source, show_keyword=show_keyword))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_new_items_md(report_data: Dict) -> str:
    if not report_data.get("new_titles"):
        return ""

    total = report_data.get("total_new_count", 0)
    lines = [f"## 本次新增热点 (共 {total} 条)", ""]
    for source_data in report_data["new_titles"]:
        source_name = source_data.get("source_name", "")
        titles = source_data.get("titles", [])
        lines.append(f"### {source_name} · {len(titles)} 条")
        lines.append("")
        for j, title_data in enumerate(titles, 1):
            title_copy = title_data.copy()
            title_copy["is_new"] = False
            lines.append(_format_news_line(j, title_copy, show_source=False))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_rss_stats_md(stats: List[Dict], title: str) -> str:
    if not stats:
        return ""

    total_count = sum(stat.get("count", 0) for stat in stats)
    if total_count == 0:
        return ""

    lines = [f"## {title} ({total_count} 条)", ""]
    for stat in stats:
        keyword = stat.get("word", "")
        titles = stat.get("titles", [])
        if not titles:
            continue
        lines.append(f"### {keyword} ({len(titles)} 条)")
        lines.append("")
        for j, title_data in enumerate(titles, 1):
            item_title = title_data.get("title", "")
            url = title_data.get("url", "") or title_data.get("mobile_url", "")
            time_display = title_data.get("time_display", "")
            source_name = title_data.get("source_name", "")
            meta = [p for p in [time_display, source_name] if p]
            if url:
                line = f"{j}. [{item_title}]({url})"
            else:
                line = f"{j}. {item_title}"
            if meta:
                line += f"  `{' | '.join(meta)}`"
            lines.append(line)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_standalone_md(data: Optional[Dict]) -> str:
    if not data:
        return ""

    platforms = data.get("platforms", [])
    rss_feeds = data.get("rss_feeds", [])
    if not platforms and not rss_feeds:
        return ""

    lines = ["## 独立展示区", ""]

    for platform in platforms:
        name = platform.get("name", platform.get("id", ""))
        items = platform.get("items", [])
        if not items:
            continue
        lines.append(f"### {name} ({len(items)} 条)")
        lines.append("")
        for j, item in enumerate(items, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            rank = item.get("rank")
            meta = []
            if rank is not None:
                meta.append(f"#{rank}")
            if url:
                line = f"{j}. [{title}]({url})"
            else:
                line = f"{j}. {title}"
            if meta:
                line += f"  `{' | '.join(meta)}`"
            lines.append(line)
        lines.append("")

    for feed in rss_feeds:
        name = feed.get("name", feed.get("id", ""))
        items = feed.get("items", [])
        if not items:
            continue
        lines.append(f"### {name} ({len(items)} 条)")
        lines.append("")
        for j, item in enumerate(items, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            published_at = item.get("published_at", "")
            author = item.get("author", "")
            meta = [p for p in [published_at, author] if p]
            if url:
                line = f"{j}. [{title}]({url})"
            else:
                line = f"{j}. {title}"
            if meta:
                line += f"  `{' | '.join(meta)}`"
            lines.append(line)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_ai_analysis_md(ai_analysis: Any) -> str:
    if not ai_analysis:
        return ""

    if not getattr(ai_analysis, "success", False):
        if getattr(ai_analysis, "skipped", False):
            return f"## AI 分析\n\n> {ai_analysis.error}\n"
        error_msg = getattr(ai_analysis, "error", None) or "未知错误"
        return f"## AI 分析\n\n> AI 分析失败: {error_msg}\n"

    lines = ["## AI 热点分析", ""]

    sections = [
        ("核心热点态势", getattr(ai_analysis, "core_trends", "")),
        ("舆论风向争议", getattr(ai_analysis, "sentiment_controversy", "")),
        ("异动与弱信号", getattr(ai_analysis, "signals", "")),
        ("RSS 深度洞察", getattr(ai_analysis, "rss_insights", "")),
        ("研判策略建议", getattr(ai_analysis, "outlook_strategy", "")),
    ]
    for title, content in sections:
        if content:
            lines.extend([f"### {title}", "", _format_list_content(content), ""])

    summaries = getattr(ai_analysis, "standalone_summaries", None) or {}
    if summaries:
        lines.append("### 独立源点速览")
        lines.append("")
        for source_name, summary in summaries.items():
            if summary:
                lines.extend([f"**[{source_name}]:**", summary, ""])

    return "\n".join(lines).rstrip() + "\n"


def render_markdown_content(
    report_data: Dict,
    total_titles: int,
    mode: str = "daily",
    *,
    region_order: Optional[List[str]] = None,
    get_time_func: Optional[Callable[[], datetime]] = None,
    rss_items: Optional[List[Dict]] = None,
    rss_new_items: Optional[List[Dict]] = None,
    display_mode: str = "keyword",
    standalone_data: Optional[Dict] = None,
    ai_analysis: Optional[Any] = None,
    show_new_section: bool = True,
    period_name: Optional[str] = None,
) -> str:
    """渲染完整 Markdown 报告内容"""
    if region_order is None:
        region_order = DEFAULT_REGION_ORDER

    now = get_time_func() if get_time_func else datetime.now()
    mode_display = _MODE_DISPLAY.get(mode, mode)
    title_suffix = f" · {period_name}" if period_name else ""

    hot_news_count = sum(len(stat["titles"]) for stat in report_data.get("stats", []))
    new_count = report_data.get("total_new_count", 0)
    rss_new_count = sum(len(stat.get("titles", [])) for stat in (rss_new_items or []))
    hotlist_total = report_data.get("hotlist_total", total_titles)
    rss_matched = report_data.get("rss_matched_count", 0)
    rss_total = report_data.get("rss_total_count", 0)

    lines = [
        f"# TrendRadar 热点报告{title_suffix}",
        "",
        f"- **报告类型**: {mode_display}",
        f"- **生成时间**: {now.strftime('%Y-%m-%d %H:%M')}",
        f"- **热榜命中**: {hot_news_count} / {hotlist_total}",
    ]

    if rss_total or rss_matched:
        lines.append(f"- **RSS 命中**: {rss_matched} / {rss_total}")
    if new_count + rss_new_count:
        lines.append(f"- **新增热点**: {new_count} + {rss_new_count}")
    else:
        lines.append("- **新增热点**: 0")

    if ai_analysis and getattr(ai_analysis, "success", False):
        analyzed = getattr(ai_analysis, "analyzed_news", 0)
        lines.append(f"- **AI 分析**: {analyzed} 条")
    lines.append("")

    if report_data.get("failed_ids"):
        lines.extend(["## 请求失败的平台", ""])
        for failed_id in report_data["failed_ids"]:
            lines.append(f"- {failed_id}")
        lines.append("")

    region_contents = {
        "hotlist": _render_hotlist_md(report_data, display_mode),
        "rss": _render_rss_stats_md(rss_items or [], "RSS 订阅更新"),
        "new_items": (
            _render_new_items_md(report_data) if show_new_section else "",
            _render_rss_stats_md(rss_new_items or [], "RSS 新增更新"),
        ),
        "standalone": _render_standalone_md(standalone_data),
        "ai_analysis": _render_ai_analysis_md(ai_analysis),
    }

    has_content = False
    for region in region_order:
        content = region_contents.get(region, "")
        if region == "new_items":
            new_md, rss_new_md = content
            for part in (new_md, rss_new_md):
                if part:
                    if has_content:
                        lines.append("")
                    lines.append(part.rstrip())
                    has_content = True
        elif content:
            if has_content:
                lines.append("")
            lines.append(content.rstrip())
            has_content = True

    if not has_content:
        if mode == "incremental":
            empty_msg = "增量模式下暂无新增匹配的热点词汇"
        elif mode == "current":
            empty_msg = "当前榜单模式下暂无匹配的热点词汇"
        else:
            empty_msg = "暂无匹配的热点词汇"
        lines.extend(["## 报告内容", "", empty_msg, ""])

    lines.extend([
        "",
        "---",
        "",
        f"*由 TrendRadar 生成 · {now.strftime('%Y-%m-%d %H:%M:%S')}*",
        "",
    ])
    return "\n".join(lines)
