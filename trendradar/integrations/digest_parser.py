# coding=utf-8
"""
全球新闻日报解析（轻量）

将下载的 HTML/Markdown/文本附件转为结构化摘要，供报告融合评估与后续集成。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from trendradar.integrations.digest_models import (
    DigestArticle,
    DigestIssue,
    DigestSourceGroup,
)


@dataclass
class DigestItem:
    """单条新闻/段落"""

    title: str
    summary: str = ""
    url: str = ""
    category: str = ""


@dataclass
class ParsedDigest:
    """解析后的日报"""

    source_path: str
    format: str
    title: str = ""
    items: List[DigestItem] = field(default_factory=list)
    raw_text: str = ""
    parse_quality: str = "unknown"  # good | partial | raw_only


def parse_digest_file(path: Path, *, newsletter_id: str = "") -> ParsedDigest:
    """根据扩展名解析日报文件"""
    suffix = path.suffix.lower()
    if suffix in (".md", ".markdown", ".txt"):
        # 优先尝试“全文日报”结构（## N. 标题 + **Source** + 正文）
        full = _try_parse_full_md(path)
        if full is not None:
            return full
        parsed = _parse_markdown_or_text(path)
        if newsletter_id == "hugin_munin" or "hugin" in parsed.title.lower():
            return _enrich_hugin_munin(parsed, path)
        return parsed
    if suffix in (".html", ".htm"):
        return _parse_html(path)
    if suffix == ".pdf":
        return ParsedDigest(
            source_path=str(path),
            format="pdf",
            title=path.stem,
            parse_quality="raw_only",
            raw_text=f"[PDF 附件需额外依赖解析: {path.name}]",
        )
    content = path.read_text(encoding="utf-8", errors="replace")
    return ParsedDigest(
        source_path=str(path),
        format=suffix or "unknown",
        title=path.stem,
        raw_text=content[:8000],
        parse_quality="raw_only",
    )


def _enrich_hugin_munin(parsed: ParsedDigest, path: Path) -> ParsedDigest:
    """Hugin & Munin MD 附件：识别 ### 小节与带摘要的条目"""
    content = path.read_text(encoding="utf-8", errors="replace")
    items: List[DigestItem] = []
    current_category = ""
    skip_sections = {"目录", "导读", "本期导读", "订阅", "关于"}

    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## "):
            heading = stripped[3:].strip()
            if any(skip in heading for skip in skip_sections):
                current_category = ""
            else:
                current_category = heading
            continue
        if stripped.startswith("### "):
            current_category = stripped[4:].strip()
            continue
        if stripped.startswith("> "):
            # 邮件正文简介风格blockquote，跳过
            continue
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            body = re.sub(r"^[-*]\s+", "", stripped)
            body = re.sub(r"^\d+\.\s+", "", body)
            url = _extract_url(body)
            title_text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", body)
            title_text = re.sub(r"https?://\S+", "", title_text).strip()
            if len(title_text) >= 6:
                items.append(DigestItem(title=title_text[:300], url=url, category=current_category))

    if len(items) > len(parsed.items):
        parsed.items = items[:200]
        parsed.parse_quality = "good" if len(items) >= 10 else "partial"
    return parsed


def _parse_markdown_or_text(path: Path) -> ParsedDigest:
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    title = path.stem
    for line in lines[:20]:
        if line.startswith("# "):
            title = line[2:].strip()
            break

    items: List[DigestItem] = []
    current_category = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("## ") and not stripped.startswith("### "):
            current_category = stripped[3:].strip()
            continue
        if re.match(r"^[-*]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
            body = re.sub(r"^[-*]\s+", "", stripped)
            body = re.sub(r"^\d+\.\s+", "", body)
            url = _extract_url(body)
            title_text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", body)
            title_text = re.sub(r"https?://\S+", "", title_text).strip()
            if len(title_text) >= 8:
                items.append(
                    DigestItem(
                        title=title_text[:300],
                        summary="",
                        url=url,
                        category=current_category,
                    )
                )

    quality = "good" if len(items) >= 3 else ("partial" if items else "raw_only")
    return ParsedDigest(
        source_path=str(path),
        format="markdown" if path.suffix.lower() in (".md", ".markdown") else "text",
        title=title,
        items=items[:100],
        raw_text=content[:8000] if not items else "",
        parse_quality=quality,
    )


def _parse_html(path: Path) -> ParsedDigest:
    content = path.read_text(encoding="utf-8", errors="replace")
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.I)
    title = unescape(title_match.group(1).strip()) if title_match else path.stem

    items: List[DigestItem] = []
    # 常见邮件 HTML：h2/h3 为区块，a 为标题链接
    for block in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", content, re.I | re.S):
        category = _strip_tags(block).strip()
        if category:
            current_category = category
        else:
            current_category = ""

    for match in re.finditer(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', content, re.I | re.S):
        url, inner = match.group(1), match.group(2)
        text = _strip_tags(inner).strip()
        if len(text) < 8 or url.startswith("mailto:"):
            continue
        if any(skip in text.lower() for skip in ("unsubscribe", "退订", "view in browser")):
            continue
        items.append(DigestItem(title=text[:300], url=url, category=""))

    # 去重
    seen = set()
    unique: List[DigestItem] = []
    for item in items:
        key = (item.title, item.url)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    quality = "good" if len(unique) >= 5 else ("partial" if unique else "raw_only")
    return ParsedDigest(
        source_path=str(path),
        format="html",
        title=title,
        items=unique[:100],
        raw_text=_strip_tags(content)[:4000] if not unique else "",
        parse_quality=quality,
    )


def _strip_tags(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(re.sub(r"\s+", " ", text))
    return text.strip()


def _extract_url(text: str) -> str:
    md = re.search(r"\((https?://[^\)]+)\)", text)
    if md:
        return md.group(1)
    plain = re.search(r"https?://\S+", text)
    return plain.group(0).rstrip(".,)") if plain else ""


# ==========================================================================
# 全文日报解析（## N. 标题 + **Source** + 正文）
# ==========================================================================

# 正文中的广告 / 订阅 / 推广噪声片段（小写匹配）
_AD_SUBSTRINGS = (
    "already subscribe to an axios newsletter",
    "log in here",
    "make busy mornings simpler",
    "understand congress through the lens",
    "catch up after the closing bell",
    "companies move faster when talent",
    "small businesses are a key part",
    "see how it works",
    "see the report",
    "get the report",
    "safeframe container",
    "ad 0 seconds of",
    "volume 0%",
    "subscribe to read",
    "sign up for",
    "view in browser",
    "linkcopy link",
    "bookmarksave",
)

_HEADING_RE = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_SOURCE_RE = re.compile(r"^\*\*Source\*\*\s*[:：]\s*(.+?)\s*$", re.MULTILINE)


def _is_noise_paragraph(text: str) -> bool:
    """判断段落是否为广告 / 乱码追踪串"""
    low = text.lower()
    if any(sub in low for sub in _AD_SUBSTRINGS):
        return True
    if not text:
        return True
    # 乱码追踪串：符号占比过高
    symbols = sum(1 for c in text if c in "%&+!=*<>?#@^~`/\\|")
    if len(text) > 30 and symbols / len(text) > 0.10:
        return True
    # 拼接导航串（如 "Red LineAirport AnxietySecret Life..."）：小写后紧跟大写过多
    camel_transitions = len(re.findall(r"[a-z][A-Z]", text))
    if camel_transitions >= 6:
        return True
    return False


def _clean_body(raw: str) -> str:
    """清洗单篇正文：去广告段落、合并有效段落"""
    raw = raw.replace("\r\n", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    kept: List[str] = []
    for para in paragraphs:
        para = para.strip().strip("-").strip()
        if not para or para == "---":
            continue
        if _is_noise_paragraph(para):
            continue
        kept.append(re.sub(r"\s+", " ", para))
    if not kept:
        return ""
    # 通常最长段落为正文主体；保留所有有效段落但去重
    seen = set()
    unique = []
    for para in kept:
        if para in seen:
            continue
        seen.add(para)
        unique.append(para)
    return "\n\n".join(unique)


def _parse_full_md_articles(content: str, date: str) -> List[DigestArticle]:
    """从 MD 全文按 `## N. 标题` 切分文章"""
    articles: List[DigestArticle] = []
    matches = list(_HEADING_RE.finditer(content))
    for i, match in enumerate(matches):
        index = int(match.group(1))
        title_en = match.group(2).strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        block = content[start:end]

        source = ""
        src_match = _SOURCE_RE.search(block)
        if src_match:
            source = src_match.group(1).strip()
            block = block[: src_match.start()] + block[src_match.end():]

        body = _clean_body(block)
        articles.append(
            DigestArticle(
                index=index,
                title_en=title_en,
                source=source,
                body=body,
                date=date,
            )
        )
    return articles


def _parse_intro_toc(content: str) -> "OrderedDict[str, List[Tuple[str, str]]]":
    """从导读 HTML 抽取 (来源 -> [(中文标题, 英文标题), ...])，保持文档顺序"""
    groups: "OrderedDict[str, List[Tuple[str, str]]]" = OrderedDict()

    # 来源分组标记位置
    source_re = re.compile(
        r'class="toc-source"[^>]*>(.*?)<span class="count"', re.S
    )
    sources = [
        (m.start(), _strip_tags(m.group(1)).strip())
        for m in source_re.finditer(content)
    ]
    if not sources:
        return groups

    # 条目（中文标题 + 可选英文标题）位置
    item_re = re.compile(
        r'class="toc-title"[^>]*>(.*?)</span>'
        r'(?:\s*<span class="toc-title-en"[^>]*>(.*?)</span>)?',
        re.S,
    )
    for src_pos, src_name in sources:
        groups.setdefault(src_name, [])

    for m in item_re.finditer(content):
        pos = m.start()
        # 找到该条目所属的最近来源
        owner = sources[0][1]
        for src_pos, src_name in sources:
            if src_pos <= pos:
                owner = src_name
            else:
                break
        title_zh = _strip_tags(m.group(1)).strip()
        title_en = _strip_tags(m.group(2)).strip() if m.group(2) else ""
        if title_zh:
            groups.setdefault(owner, []).append((title_zh, title_en))
    return groups


def _normalize_source(name: str) -> str:
    return re.sub(r"\s+", "", name or "").lower()


def _align_articles(
    articles: List[DigestArticle],
    toc: "OrderedDict[str, List[Tuple[str, str]]]",
) -> int:
    """按 (来源, 组内序号) 将导读中文标题对齐到 MD 文章，返回成功对齐数"""
    if not toc:
        return 0

    toc_norm: Dict[str, List[Tuple[str, str]]] = {}
    for src, items in toc.items():
        toc_norm.setdefault(_normalize_source(src), []).extend(items)

    # MD 文章按来源顺序分组（保留出现顺序）
    md_by_source: "OrderedDict[str, List[DigestArticle]]" = OrderedDict()
    for art in articles:
        md_by_source.setdefault(_normalize_source(art.source), []).append(art)

    aligned = 0
    for src_norm, md_list in md_by_source.items():
        intro_list = toc_norm.get(src_norm, [])
        for i, art in enumerate(md_list):
            if i < len(intro_list):
                zh, en = intro_list[i]
                art.title_zh = zh
                if en and not art.title_en:
                    art.title_en = en
                aligned += 1
    return aligned


# 规则预分类关键词（首个命中类目生效）
_CATEGORY_RULES: List[Tuple[str, Tuple[str, ...]]] = [
    ("AI与科技", ("ai ", " ai", "chip", "semiconductor", "nvidia", "openai",
                  "data center", "software", "算力", "芯片", "半导体", "人工智能", "模型")),
    ("能源与大宗", ("oil", "crude", "opec", "energy", "natural gas", "hormuz",
                   "commodity", "石油", "原油", "能源", "天然气", "霍尔木兹", "大宗")),
    ("金融市场", ("stock", "bond", "market", "fed ", "rate", "inflation", "ipo",
                 "yield", "earnings", "dollar", "股", "债", "美联储", "通胀", "利率", "上市", "财报")),
    ("地缘政治", ("iran", "israel", "gaza", "ukraine", "russia", "nato", "war",
                 "military", "sanction", "tariff", "election", "congress", "trump",
                 "伊朗", "以色列", "俄", "乌克兰", "战争", "制裁", "关税", "选举", "国会", "特朗普")),
    ("中国与亚洲", ("china", "beijing", "taiwan", "japan", "korea", "india", "asia",
                   "中国", "北京", "台湾", "日本", "韩国", "印度", "亚洲")),
    ("产业公司", ("company", "startup", "merger", "acquisition", "ceo", "spacex",
                 "tesla", "公司", "并购", "创业", "收购")),
    ("社会文化", ("culture", "sports", "health", "holiday", "film", "music",
                 "文化", "体育", "健康", "节日")),
]


def _categorize_article(article: DigestArticle) -> str:
    text = f"{article.title_en} {article.title_zh} {article.body[:200]}".lower()
    for category, keywords in _CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return category
    return "综合"


def _extract_date_from_name(path: Path) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return m.group(1) if m else ""


def _try_parse_full_md(path: Path) -> Optional[ParsedDigest]:
    """检测并解析全文日报；非该结构则返回 None（回退旧逻辑）"""
    content = path.read_text(encoding="utf-8", errors="replace")
    headings = _HEADING_RE.findall(content)
    if len(headings) < 5:
        return None

    date = _extract_date_from_name(path)
    articles = _parse_full_md_articles(content, date)
    for art in articles:
        art.category = _categorize_article(art)

    title = path.stem
    first_line = content.splitlines()[0].strip() if content else ""
    if first_line.startswith("# "):
        title = first_line[2:].strip()

    items = [
        DigestItem(
            title=art.title_en or art.title_zh,
            summary=art.body[:280],
            url=art.url,
            category=art.source or art.category,
        )
        for art in articles
    ]
    return ParsedDigest(
        source_path=str(path),
        format="markdown",
        title=title,
        items=items[:300],
        parse_quality="good" if len(items) >= 10 else "partial",
    )


def parse_digest_issue(
    md_path: Path,
    intro_path: Optional[Path] = None,
) -> DigestIssue:
    """完整解析一期日报：MD 全文 + 导读 HTML，按来源对齐中文标题。

    这是 B站充电日报产品的主入口，返回结构化 DigestIssue。
    """
    md_path = Path(md_path)
    content = md_path.read_text(encoding="utf-8", errors="replace")
    date = _extract_date_from_name(md_path)

    articles = _parse_full_md_articles(content, date)
    for art in articles:
        art.category = _categorize_article(art)

    title = md_path.stem
    first_line = content.splitlines()[0].strip() if content else ""
    if first_line.startswith("# "):
        title = first_line[2:].strip()

    aligned = 0
    if intro_path and Path(intro_path).exists():
        intro_content = Path(intro_path).read_text(encoding="utf-8", errors="replace")
        toc = _parse_intro_toc(intro_content)
        aligned = _align_articles(articles, toc)

    # 来源分组（保留出现顺序）
    groups: "OrderedDict[str, DigestSourceGroup]" = OrderedDict()
    for art in articles:
        key = art.source or "未知来源"
        if key not in groups:
            groups[key] = DigestSourceGroup(source=key)
        groups[key].articles.append(art)
    for group in groups.values():
        group.count = len(group.articles)

    quality = "good" if len(articles) >= 10 else ("partial" if articles else "raw_only")
    return DigestIssue(
        date=date,
        title=title,
        md_path=str(md_path),
        intro_path=str(intro_path) if intro_path else "",
        articles=articles,
        source_groups=list(groups.values()),
        total_articles=len(articles),
        parse_quality=quality,
        aligned_zh_count=aligned,
    )


def digest_to_markdown_section(parsed: ParsedDigest, *, max_items: int = 30, source_name: str = "Hugin & Munin") -> str:
    """将解析结果转为可嵌入 TrendRadar MD 报告的区块"""
    lines = [f"## {source_name} · {parsed.title}", ""]
    if parsed.items:
        by_cat: dict = {}
        for item in parsed.items[:max_items]:
            cat = item.category or "综合"
            by_cat.setdefault(cat, []).append(item)
        for cat, cat_items in by_cat.items():
            lines.append(f"### {cat} ({len(cat_items)} 条)")
            lines.append("")
            for i, item in enumerate(cat_items, 1):
                if item.url:
                    lines.append(f"{i}. [{item.title}]({item.url})")
                else:
                    lines.append(f"{i}. {item.title}")
            lines.append("")
    elif parsed.raw_text:
        lines.append(parsed.raw_text[:3000])
        lines.append("")
    lines.append(f"*来源: `{Path(parsed.source_path).name}` · 解析质量: {parsed.parse_quality}*")
    return "\n".join(lines)
