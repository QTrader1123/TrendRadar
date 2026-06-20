# coding=utf-8
"""
全球新闻深度日报 结构化数据模型

为 B站充电专属日报产品提供统一的中间表示：
- DigestArticle: 单篇文章（对齐 MD 正文与导读 HTML 的中英标题）
- DigestSourceGroup: 按来源分组
- DigestIssue: 一期日报的完整结构化结果
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List


@dataclass
class DigestArticle:
    """单篇深度文章"""

    index: int = 0  # 在 MD 中的序号（## N.）
    title_en: str = ""  # 英文标题（MD 标题 / 导读 toc-title-en）
    title_zh: str = ""  # 中文标题（导读 toc-title）
    source: str = ""  # 来源出版方，如 AXIOS / Bloomberg
    body: str = ""  # 清洗后的正文
    url: str = ""
    category: str = ""  # 规则预分类主题
    date: str = ""

    @property
    def body_chars(self) -> int:
        return len(self.body)

    @property
    def title(self) -> str:
        """优先中文标题，回退英文标题"""
        return self.title_zh or self.title_en


@dataclass
class DigestSourceGroup:
    """按来源分组"""

    source: str = ""
    count: int = 0  # 导读标注的篇数
    articles: List[DigestArticle] = field(default_factory=list)


@dataclass
class DigestIssue:
    """一期全球新闻深度日报的结构化结果"""

    date: str = ""
    title: str = ""
    md_path: str = ""
    intro_path: str = ""
    articles: List[DigestArticle] = field(default_factory=list)
    source_groups: List[DigestSourceGroup] = field(default_factory=list)
    total_articles: int = 0
    parse_quality: str = "unknown"  # good | partial | raw_only
    aligned_zh_count: int = 0  # 成功对齐到中文标题的文章数

    def to_dict(self) -> Dict:
        return asdict(self)

    def category_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for article in self.articles:
            key = article.category or "未分类"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def source_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for article in self.articles:
            key = article.source or "未知来源"
            counts[key] = counts.get(key, 0) + 1
        return counts
