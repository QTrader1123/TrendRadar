# coding=utf-8
"""全球新闻日报样例结构研究"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from html import unescape
from pathlib import Path
from typing import Dict, List, Optional

from trendradar.integrations.digest_parser import parse_digest_file


@dataclass
class SectionInfo:
    level: int
    title: str
    line_or_index: int


@dataclass
class DigestResearch:
    """样例结构分析结果"""

    source_path: str
    file_size_bytes: int
    format: str
    title: str = ""
    char_count: int = 0
    line_count: int = 0
    word_estimate: int = 0
    sections: List[SectionInfo] = field(default_factory=list)
    section_count_by_level: Dict[str, int] = field(default_factory=dict)
    link_count: int = 0
    image_count: int = 0
    table_count: int = 0
    parsed_item_count: int = 0
    categories: Dict[str, int] = field(default_factory=dict)
    parse_quality: str = "unknown"
    token_estimate: int = 0
    integration_hints: List[str] = field(default_factory=list)
    sample_items: List[str] = field(default_factory=list)


def research_digest_sample(path: Path) -> DigestResearch:
    """对单份日报样例做结构研究（不修改原文件）"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"样例不存在: {path}")

    suffix = path.suffix.lower()
    fmt = suffix.lstrip(".") or "unknown"
    size = path.stat().st_size

    if suffix == ".pdf":
        return _research_pdf(path, size)

    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    char_count = len(content)
    word_estimate = len(re.findall(r"\S+", content))

    if suffix in (".html", ".htm"):
        research = _research_html(path, content, size, fmt)
    else:
        research = _research_text(path, content, size, fmt)

    parsed = parse_digest_file(path)
    research.parsed_item_count = len(parsed.items)
    research.parse_quality = parsed.parse_quality
    research.title = parsed.title or research.title
    research.categories = dict(Counter(item.category or "未分类" for item in parsed.items))
    research.sample_items = [item.title[:120] for item in parsed.items[:8]]
    research.token_estimate = max(1, char_count // 2)  # 中文粗估
    research.integration_hints = _build_integration_hints(research)
    return research


def _research_pdf(path: Path, size: int) -> DigestResearch:
    hints = [
        "附件为 PDF，需先确认页数与是否可复制文本",
        "若 PDF 为扫描件，需 OCR；若为文本 PDF，可接入 pypdf/pdfplumber",
        "融合建议：优先提取目录/章节标题，全文仅作 AI 摘要输入而非完整嵌入报告",
    ]
    page_count = None
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        page_count = len(reader.pages)
        hints.insert(0, f"PDF 共 {page_count} 页")
    except ImportError:
        hints.insert(0, "未安装 pypdf，无法读取 PDF 页数（pip install pypdf）")
    except Exception as exc:
        hints.insert(0, f"PDF 读取失败: {exc}")

    return DigestResearch(
        source_path=str(path),
        file_size_bytes=size,
        format="pdf",
        title=path.stem,
        integration_hints=hints,
        token_estimate=size // 4,
    )


def _research_html(path: Path, content: str, size: int, fmt: str) -> DigestResearch:
    title_match = re.search(r"<title[^>]*>([^<]+)</title>", content, re.I)
    title = unescape(title_match.group(1).strip()) if title_match else path.stem

    sections: List[SectionInfo] = []
    for i, match in enumerate(re.finditer(r"<h([1-4])[^>]*>(.*?)</h\1>", content, re.I | re.S)):
        level = int(match.group(1))
        text = _strip_html(match.group(2))[:200]
        if text:
            sections.append(SectionInfo(level=level, title=text, line_or_index=i))

    link_count = len(re.findall(r'<a[^>]+href=', content, re.I))
    image_count = len(re.findall(r"<img[^>]+>", content, re.I))
    table_count = len(re.findall(r"<table[^>]*>", content, re.I))

    return DigestResearch(
        source_path=str(path),
        file_size_bytes=size,
        format=fmt,
        title=title,
        char_count=len(content),
        line_count=content.count("\n") + 1,
        word_estimate=len(re.findall(r"\S+", _strip_html(content))),
        sections=sections[:80],
        section_count_by_level=_count_levels(sections),
        link_count=link_count,
        image_count=image_count,
        table_count=table_count,
    )


def _research_text(path: Path, content: str, size: int, fmt: str) -> DigestResearch:
    lines = content.splitlines()
    title = path.stem
    sections: List[SectionInfo] = []

    for idx, line in enumerate(lines):
        m = re.match(r"^(#{1,4})\s+(.+)$", line.strip())
        if m:
            sections.append(
                SectionInfo(level=len(m.group(1)), title=m.group(2).strip()[:200], line_or_index=idx + 1)
            )
            if m.group(1) == "#" and title == path.stem:
                title = m.group(2).strip()

    link_count = len(re.findall(r"https?://\S+", content))
    return DigestResearch(
        source_path=str(path),
        file_size_bytes=size,
        format=fmt,
        title=title,
        char_count=len(content),
        line_count=len(lines),
        word_estimate=len(re.findall(r"\S+", content)),
        sections=sections[:80],
        section_count_by_level=_count_levels(sections),
        link_count=link_count,
    )


def _count_levels(sections: List[SectionInfo]) -> Dict[str, int]:
    c: Counter = Counter()
    for s in sections:
        c[f"h{s.level}"] += 1
    return dict(c)


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", text)).strip()


def _build_integration_hints(r: DigestResearch) -> List[str]:
    hints: List[str] = []

    if r.file_size_bytes > 500_000:
        hints.append(f"体积较大（{r.file_size_bytes // 1024} KB），不宜全文嵌入 TrendRadar 报告")
    if r.token_estimate > 8000:
        hints.append(f"粗估 token ~{r.token_estimate}，超过单次 AI 上下文舒适区，需分层/摘要")
    if r.section_count_by_level:
        top_sections = [s.title for s in r.sections if s.level <= 2][:12]
        hints.append(f"检测到 {sum(r.section_count_by_level.values())} 个章节，顶层包括: {', '.join(top_sections[:5])}")
    if r.parsed_item_count >= 30:
        hints.append(f"可解析出约 {r.parsed_item_count}+ 条新闻链接，适合「按章节折叠 + 每章限条数」展示")
    elif r.parsed_item_count > 0:
        hints.append(f"仅解析出 {r.parsed_item_count} 条结构化条目，可能需要定制解析规则")
    else:
        hints.append("当前通用解析器未能提取条目，需针对该日报 HTML/排版定制 parser")

    if r.categories and len(r.categories) > 1:
        hints.append(f"分类维度约 {len(r.categories)} 个: {', '.join(list(r.categories.keys())[:6])}")

    if r.image_count > 20:
        hints.append(f"含 {r.image_count} 张图片，报告融合时建议只保留文字+链接，忽略 inline 图片")
    if r.table_count > 0:
        hints.append(f"含 {r.table_count} 个表格，可能需要单独保留为附录或转 Markdown 表格")

    if r.format == "markdown" and "hugin" in r.title.lower():
        hints.insert(0, "Hugin & Munin 格式：MD 附件为主内容，邮件 *_intro.html 含简介+目录")
        hints.insert(1, "融合建议：报告顶部放简介（intro），正文按 MD 章节折叠展示")

    hints.append("建议策略：样例研究确认结构后，再决定「摘要级融合」还是「分章节完整嵌入」")
    return hints


def format_research_report(r: DigestResearch) -> str:
    """人类可读的研究报告"""
    lines = [
        "=" * 60,
        "全球新闻日报 · 样例结构研究",
        "=" * 60,
        f"文件: {r.source_path}",
        f"格式: {r.format} | 大小: {r.file_size_bytes // 1024} KB",
        f"标题: {r.title}",
        f"字符: {r.char_count:,} | 行数: {r.line_count:,} | 词块: {r.word_estimate:,}",
        f"链接: {r.link_count} | 图片: {r.image_count} | 表格: {r.table_count}",
        f"解析条目: {r.parsed_item_count} | 质量: {r.parse_quality} | token 粗估: {r.token_estimate:,}",
        "",
    ]

    if r.section_count_by_level:
        lines.append("章节统计:")
        for level, count in sorted(r.section_count_by_level.items()):
            lines.append(f"  {level}: {count}")
        lines.append("")
        lines.append("章节预览（前 15）:")
        for s in r.sections[:15]:
            indent = "  " * (s.level - 1)
            lines.append(f"  {indent}[{s.level}] {s.title}")
        lines.append("")

    if r.categories:
        lines.append("分类分布:")
        for cat, cnt in sorted(r.categories.items(), key=lambda x: -x[1])[:15]:
            lines.append(f"  • {cat}: {cnt}")
        lines.append("")

    if r.sample_items:
        lines.append("条目样例（前 8）:")
        for i, t in enumerate(r.sample_items, 1):
            lines.append(f"  {i}. {t}")
        lines.append("")

    lines.append("融合建议:")
    for h in r.integration_hints:
        lines.append(f"  → {h}")
    lines.append("=" * 60)
    return "\n".join(lines)


def save_research_report(r: DigestResearch, output_dir: Path) -> Path:
    """保存研究报告（txt + json）"""
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(r.source_path).stem
    txt_path = output_dir / f"{stem}_research.txt"
    json_path = output_dir / f"{stem}_research.json"

    txt_path.write_text(format_research_report(r), encoding="utf-8")

    payload = asdict(r)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return txt_path
