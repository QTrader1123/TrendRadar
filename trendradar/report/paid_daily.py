# coding=utf-8
"""
全球深度信号日报 - B站充电专属 Markdown 渲染器

输入：DigestIssue（全球深度语料）+ 国内热点 + 交叉分析结果
输出：付费专属成品 MD、免费预览 MD、结构化 JSON

独立于普通 TrendRadar 报告渲染（report/markdown.py），因为这是一个新内容产品形态。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from trendradar.integrations.digest_models import DigestArticle, DigestIssue
from trendradar.integrations.digest_summarizer import (
    CrossAnalysis,
    Hotspot,
    build_topic_packages,
)

PRODUCT_NAME = "全球深度信号日报"
PRODUCT_SUBTITLE = "从中文热榜到全球叙事的一日研判"

# 渲染时展示的类目顺序与中文小节标题
_SECTION_ORDER: List[tuple] = [
    ("地缘政治", "地缘与安全风险"),
    ("能源与大宗", "能源与大宗商品"),
    ("AI与科技", "AI 与科技产业信号"),
    ("金融市场", "金融市场与资产影响"),
    ("中国与亚洲", "中国与亚洲视角"),
    ("产业公司", "产业与公司动态"),
    ("社会文化", "社会与文化"),
]


def _fallback_brief(issue: DigestIssue, cross: CrossAnalysis) -> str:
    """AI 未启用时的规则版「今日总判断」"""
    parts: List[str] = []
    if cross.resonance:
        top = cross.resonance[0]
        parts.append(
            f"今日国内外共同聚焦于「{top.keyword}」等议题，"
            f"全球深度语料以 {issue.total_articles} 篇覆盖多源视角。"
        )
    else:
        parts.append(
            f"今日全球深度语料共 {issue.total_articles} 篇，国内热榜与全球主线交集有限。"
        )
    if cross.global_only:
        kws = "、".join(t.keyword for t in cross.global_only[:3])
        parts.append(f"值得注意的是，「{kws}」在全球被重点讨论，但国内平台暂未充分发酵。")
    if cross.domestic_only:
        kws = "、".join(t.keyword for t in cross.domestic_only[:3])
        parts.append(f"而「{kws}」是当前中文语境的本土热点。")
    return "".join(parts)


def _best_snippet(body: str, *, limit: int = 150) -> str:
    """从正文挑选最具信息量的段落（优先含 Why it matters，其次最长段落）"""
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""
    for para in paragraphs:
        if "why it matters" in para.lower():
            return para[:limit].strip()
    best = max(paragraphs, key=len)
    return best[:limit].strip()


def _article_line(article: DigestArticle, *, with_summary: bool = False) -> str:
    title = article.title
    src = f" `{article.source}`" if article.source else ""
    line = f"- {title}{src}"
    if with_summary and article.body:
        snippet = _best_snippet(article.body)
        if snippet:
            line += f"\n  - {snippet}"
    return line


def generate_paid_daily(
    issue: DigestIssue,
    hotspots: List[Hotspot],
    cross: CrossAnalysis,
    *,
    ai_brief: str = "",
    theme_stories: Optional[List[str]] = None,
    max_main_lines: int = 5,
) -> str:
    """渲染充电专属完整版 Markdown"""
    date = issue.date or "未知日期"
    lines: List[str] = []
    lines.append(f"# {PRODUCT_NAME} · {date}")
    lines.append("")
    lines.append(f"> {PRODUCT_SUBTITLE}")
    lines.append("")

    # 今日总判断
    lines.append("## 今日总判断")
    lines.append("")
    lines.append(ai_brief.strip() if ai_brief.strip() else _fallback_brief(issue, cross))
    lines.append("")

    # 今日主题故事（AI 归并，可选）
    if theme_stories:
        lines.append("## 今日主题故事")
        lines.append("")
        lines.extend(theme_stories)
        lines.append("")

    # 全球主线
    lines.append("## 全球主线")
    lines.append("")
    main_topics = (cross.resonance + cross.global_only)[:max_main_lines]
    if main_topics:
        for i, topic in enumerate(main_topics, 1):
            src = f"（来源：{', '.join(topic.global_sources[:4])}）" if topic.global_sources else ""
            sample = topic.global_titles[0] if topic.global_titles else topic.keyword
            tag = "国内外共振" if topic.hot_count else "全球重点"
            lines.append(f"{i}. **{topic.keyword}**（{tag}）：{sample}{src}")
    else:
        for i, art in enumerate(issue.articles[:max_main_lines], 1):
            lines.append(f"{i}. **{art.title}** `{art.source}`")
    lines.append("")

    # 中文热榜与全球叙事交叉
    lines.append("## 中文热榜与全球叙事交叉")
    lines.append("")
    if cross.resonance:
        lines.append("### 同题共振（国内外都在关注）")
        lines.append("")
        for topic in cross.resonance:
            lines.append(f"- **{topic.keyword}**")
            if topic.hot_titles:
                lines.append(f"  - 国内热榜：{topic.hot_titles[0]}")
            if topic.global_titles:
                srcs = f"（{', '.join(topic.global_sources[:3])}）" if topic.global_sources else ""
                lines.append(f"  - 全球视角：{topic.global_titles[0]}{srcs}")
        lines.append("")
    if cross.domestic_only:
        lines.append("### 内热外冷（国内热、全球弱关注）")
        lines.append("")
        for topic in cross.domestic_only:
            lines.append(f"- **{topic.keyword}**：{topic.hot_titles[0] if topic.hot_titles else ''}")
        lines.append("")
    if cross.global_only:
        lines.append("### 外热内冷（全球重要、国内暂未发酵）")
        lines.append("")
        for topic in cross.global_only:
            srcs = f"（{', '.join(topic.global_sources[:3])}）" if topic.global_sources else ""
            lines.append(f"- **{topic.keyword}**：{topic.global_titles[0] if topic.global_titles else ''}{srcs}")
        lines.append("")

    # 分类深度信号
    packages = build_topic_packages(issue)
    for cat_key, cat_title in _SECTION_ORDER:
        arts = packages.get(cat_key)
        if not arts:
            continue
        lines.append(f"## {cat_title}")
        lines.append("")
        for art in arts[:8]:
            lines.append(_article_line(art, with_summary=True))
        lines.append("")

    # 明日追踪清单（弱信号 = 全球重点但国内冷）
    lines.append("## 明日追踪清单")
    lines.append("")
    watch = cross.global_only[:5] or cross.resonance[:5]
    if watch:
        for topic in watch:
            lines.append(f"- {topic.keyword}：关注其是否进入中文舆论场")
    else:
        lines.append("- 暂无显著弱信号")
    lines.append("")

    # 来源索引
    lines.append("## 来源索引")
    lines.append("")
    for group in issue.source_groups:
        lines.append(f"### {group.source}（{group.count} 篇）")
        for art in group.articles[:6]:
            lines.append(f"- {art.title}")
        lines.append("")

    lines.append("---")
    lines.append(
        f"*本期基于全球深度语料 {issue.total_articles} 篇"
        f"（中文对齐 {issue.aligned_zh_count} 篇）与国内热点 {len(hotspots)} 条生成。"
        f"原文版权归各来源所有，本产品仅作摘要、索引与研判。*"
    )
    return "\n".join(lines)


def generate_detailed(issue: DigestIssue) -> str:
    """详细版：全部文章，中文标题 + 英文原题 + 清洗后正文，按来源分组完整呈现。

    定位介于「原始英文全文」与「精选充电版」之间，适合长读与归档。
    """
    date = issue.date or "未知日期"
    lines: List[str] = []
    lines.append(f"# {PRODUCT_NAME}（详细版）· {date}")
    lines.append("")
    lines.append(
        f"> 全部 {issue.total_articles} 篇 · 中文标题对齐 {issue.aligned_zh_count} 篇 · 按来源分组"
    )
    lines.append("")

    # 概览
    lines.append("## 概览")
    lines.append("")
    src_counts = issue.source_counts()
    cat_counts = issue.category_counts()
    lines.append("- 来源分布：" + "、".join(f"{k} {v}" for k, v in
                 sorted(src_counts.items(), key=lambda x: -x[1])))
    lines.append("- 类目分布：" + "、".join(f"{k} {v}" for k, v in
                 sorted(cat_counts.items(), key=lambda x: -x[1])))
    lines.append("")

    # 正文：按来源分组
    for group in issue.source_groups:
        lines.append(f"## {group.source}（{group.count} 篇）")
        lines.append("")
        for art in group.articles:
            title = art.title_zh or art.title_en
            lines.append(f"### {art.index}. {title}")
            meta = []
            if art.title_zh and art.title_en:
                meta.append(f"_{art.title_en}_")
            if art.category:
                meta.append(f"`{art.category}`")
            if meta:
                lines.append(" · ".join(meta))
            lines.append("")
            if art.body:
                for para in art.body.split("\n\n"):
                    para = para.strip()
                    if para:
                        lines.append(para)
                        lines.append("")
            else:
                lines.append("（无正文）")
                lines.append("")

    lines.append("---")
    lines.append(
        f"*详细版基于 `{Path(issue.md_path).name}` 解析生成，正文已去广告与乱码，"
        f"版权归各来源所有，仅供个人阅读与归档。*"
    )
    return "\n".join(lines)


def generate_preview(
    issue: DigestIssue,
    cross: CrossAnalysis,
    *,
    ai_brief: str = "",
) -> str:
    """免费预览版（用于动态 / 视频简介，引导充电）"""
    date = issue.date or "未知日期"
    lines: List[str] = [f"# {PRODUCT_NAME} · {date}（免费预览）", ""]
    lines.append(ai_brief.strip() if ai_brief.strip() else _fallback_brief(issue, cross))
    lines.append("")
    lines.append("**今日全球主线（节选）**")
    lines.append("")
    for topic in (cross.resonance + cross.global_only)[:3]:
        lines.append(f"- {topic.keyword}")
    lines.append("")
    if cross.global_only:
        lines.append(f"**一条值得提前关注的外热内冷信号**：{cross.global_only[0].keyword}")
        lines.append("")
    lines.append("> 完整版含国内外叙事交叉、分类深度信号与来源索引，欢迎充电解锁。")
    return "\n".join(lines)


def save_paid_daily(
    issue: DigestIssue,
    hotspots: List[Hotspot],
    cross: CrossAnalysis,
    *,
    ai_brief: str = "",
    theme_stories: Optional[List[str]] = None,
    output_root: str = "output/paid_daily",
) -> Dict[str, str]:
    """生成并归档：成品 MD、预览 MD、结构化 JSON。返回写入路径字典。"""
    date = issue.date or "unknown"
    out_dir = Path(output_root) / date
    out_dir.mkdir(parents=True, exist_ok=True)

    product_md = generate_paid_daily(
        issue, hotspots, cross, ai_brief=ai_brief, theme_stories=theme_stories
    )
    preview_md = generate_preview(issue, cross, ai_brief=ai_brief)
    detailed_md = generate_detailed(issue)

    product_path = out_dir / f"{PRODUCT_NAME}-{date}.md"
    preview_path = out_dir / f"{PRODUCT_NAME}-{date}_preview.md"
    detailed_path = out_dir / f"{PRODUCT_NAME}-{date}_详细版.md"
    json_path = out_dir / f"{PRODUCT_NAME}-{date}.json"

    product_path.write_text(product_md, encoding="utf-8")
    preview_path.write_text(preview_md, encoding="utf-8")
    detailed_path.write_text(detailed_md, encoding="utf-8")

    archive = {
        "product": PRODUCT_NAME,
        "date": date,
        "issue": issue.to_dict(),
        "hotspots": [h.__dict__ for h in hotspots],
        "cross": {
            "resonance": [t.__dict__ for t in cross.resonance],
            "domestic_only": [t.__dict__ for t in cross.domestic_only],
            "global_only": [t.__dict__ for t in cross.global_only],
        },
        "ai_brief": ai_brief,
        "theme_stories": theme_stories or [],
    }
    json_path.write_text(
        json.dumps(archive, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "product": str(product_path),
        "detailed": str(detailed_path),
        "preview": str(preview_path),
        "json": str(json_path),
    }
