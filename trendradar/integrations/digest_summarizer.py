# coding=utf-8
"""
全球深度信号日报 - 交叉分析与主题压缩

职责：
1. 读取 TrendRadar 当前国内热榜 / RSS 热点（中文语境信号）。
2. 将 DigestIssue 压缩为按主题的“主题包”，供渲染与 AI 摘要使用。
3. 计算国内热榜与全球深度语料的交叉关系：同题共振 / 内热外冷 / 外热内冷。

设计原则（见 plan「导读优先」）：默认基于导读层中文标题做交叉，不依赖 198 篇全文，
AI 仅在显式启用时介入，缺省走纯规则，保证离线可用与成本可控。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from trendradar.integrations.digest_models import DigestArticle, DigestIssue

# 主题词典类型：规范名 -> 别名元组
Lexicon = Dict[str, Tuple[str, ...]]


@dataclass
class Hotspot:
    """国内热点信号（热榜或 RSS）"""

    title: str
    platform: str = ""
    kind: str = "hotlist"  # hotlist | rss
    rank: int = 0
    url: str = ""


@dataclass
class CrossTopic:
    """一个交叉主题"""

    keyword: str
    hot_titles: List[str] = field(default_factory=list)
    global_titles: List[str] = field(default_factory=list)
    global_sources: List[str] = field(default_factory=list)

    @property
    def hot_count(self) -> int:
        return len(self.hot_titles)

    @property
    def global_count(self) -> int:
        return len(self.global_titles)


@dataclass
class CrossAnalysis:
    """交叉分析结果"""

    resonance: List[CrossTopic] = field(default_factory=list)  # 同题共振
    domestic_only: List[CrossTopic] = field(default_factory=list)  # 内热外冷
    global_only: List[CrossTopic] = field(default_factory=list)  # 外热内冷


# ==========================================================================
# 主题关键词词典（跨中英对齐，用于交叉匹配）
# canonical -> 别名（中英大小写混合，匹配时小写）
# 这是内置兜底词典；优先使用 config/paid_daily_lexicon.yaml（见 load_lexicon）。
# ==========================================================================
_ENTITY_LEXICON: Lexicon = {
    "伊朗": ("伊朗", "iran", "iranian", "tehran", "德黑兰"),
    "以色列": ("以色列", "israel", "israeli"),
    "霍尔木兹": ("霍尔木兹", "hormuz"),
    "加沙": ("加沙", "gaza"),
    "俄乌": ("俄罗斯", "乌克兰", "russia", "ukraine", "putin", "普京", "泽连斯基"),
    "特朗普": ("特朗普", "trump"),
    "美联储": ("美联储", "fed ", "federal reserve", "rate cut", "加息", "降息", "利率"),
    "关税": ("关税", "tariff", "tariffs"),
    "AI": ("人工智能", "ai ", " ai", "openai", "chatgpt", "大模型", "llm", "anthropic"),
    "芯片": ("芯片", "半导体", "chip", "semiconductor", "nvidia", "英伟达", "tsmc", "台积电"),
    "石油": ("石油", "原油", "oil", "crude", "opec", "布伦特", "brent"),
    "黄金": ("黄金", "gold", "金价"),
    "比特币": ("比特币", "bitcoin", "btc", "加密货币", "crypto"),
    "台湾": ("台湾", "taiwan", "taipei"),
    "日本": ("日本", "japan", "tokyo", "日元", "yen"),
    "韩国": ("韩国", "korea", "seoul"),
    "印度": ("印度", "india", "modi", "莫迪"),
    "欧盟": ("欧盟", "europe", "eu ", "brussels", "欧洲"),
    "马斯克": ("马斯克", "musk", "tesla", "特斯拉", "spacex"),
    "苹果": ("苹果", "apple", "iphone"),
    "股市": ("股市", "stock", "stocks", "equit", "纳斯达克", "nasdaq", "标普", "s&p", "a股", "港股"),
    "通胀": ("通胀", "inflation", "cpi", "物价"),
    "选举": ("选举", "election", "vote", "primary", "大选"),
    "气候": ("气候", "climate", "碳", "carbon", "排放", "emission"),
    "移民": ("移民", "immigration", "migrant", "border", "边境"),
}


def load_hotspots(config: Optional[Dict] = None, *, limit: int = 80) -> List[Hotspot]:
    """读取最新国内热榜 + RSS 热点。

    失败（无配置 / 无数据 / 抓取层异常）时返回空列表，保证渲染流程不被打断。
    """
    hotspots: List[Hotspot] = []
    ctx = None
    try:
        from trendradar.core import load_config
        from trendradar.context import AppContext

        ctx = AppContext(config or load_config())
        sm = ctx.get_storage_manager()

        hot = sm.get_latest_crawl_data()
        if hot:
            for source_id, items in hot.items.items():
                platform = hot.id_to_name.get(source_id, source_id)
                for item in items:
                    hotspots.append(
                        Hotspot(
                            title=item.title,
                            platform=item.source_name or platform,
                            kind="hotlist",
                            rank=getattr(item, "rank", 0) or 0,
                            url=getattr(item, "url", "") or "",
                        )
                    )

        rss = sm.get_latest_rss_data()
        if rss:
            for feed_id, items in rss.items.items():
                feed = rss.id_to_name.get(feed_id, feed_id)
                for item in items:
                    hotspots.append(
                        Hotspot(
                            title=item.title,
                            platform=item.feed_name or feed,
                            kind="rss",
                            url=getattr(item, "url", "") or "",
                        )
                    )
    except Exception as exc:  # noqa: BLE001 - 交叉分析为增值功能，不应阻断
        print(f"  [paid-daily] 读取国内热点失败，将仅用全球语料: {exc}")
    finally:
        if ctx is not None:
            try:
                ctx.cleanup()
            except Exception:  # noqa: BLE001
                pass

    return hotspots[:limit] if limit else hotspots


def load_lexicon(path: Optional[str] = None) -> Lexicon:
    """从 YAML 加载主题词典；缺省或失败时返回内置兜底词典。"""
    if not path:
        return dict(_ENTITY_LEXICON)
    p = Path(path)
    if not p.exists():
        print(f"  [paid-daily] 词典文件不存在，使用内置词典: {path}")
        return dict(_ENTITY_LEXICON)
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        lexicon: Lexicon = {}
        for canonical, aliases in data.items():
            if isinstance(aliases, str):
                aliases = [aliases]
            lexicon[str(canonical)] = tuple(str(a) for a in (aliases or []))
        return lexicon or dict(_ENTITY_LEXICON)
    except Exception as exc:  # noqa: BLE001
        print(f"  [paid-daily] 词典解析失败，使用内置词典: {exc}")
        return dict(_ENTITY_LEXICON)


def _match_entities(text: str, lexicon: Lexicon) -> List[str]:
    low = f" {text.lower()} "
    matched = []
    for canonical, aliases in lexicon.items():
        if any(alias.lower() in low for alias in aliases):
            matched.append(canonical)
    return matched


def cross_analyze(
    issue: DigestIssue,
    hotspots: List[Hotspot],
    *,
    max_per_bucket: int = 8,
    lexicon: Optional[Lexicon] = None,
) -> CrossAnalysis:
    """基于主题词典计算三类交叉关系"""
    lex = lexicon or dict(_ENTITY_LEXICON)
    # 全球语料 entity -> 文章（标题命中优先于正文命中，作为该主题代表作）
    global_map: Dict[str, List[DigestArticle]] = {}
    for art in issue.articles:
        title_ents = set(_match_entities(f"{art.title_zh} {art.title_en}", lex))
        body_ents = set(_match_entities(art.body[:200], lex))
        for ent in title_ents:
            global_map.setdefault(ent, []).insert(0, art)  # 标题命中置顶
        for ent in body_ents - title_ents:
            global_map.setdefault(ent, []).append(art)

    # 国内热点 entity -> 标题
    domestic_map: Dict[str, List[Hotspot]] = {}
    for hs in hotspots:
        for ent in _match_entities(hs.title, lex):
            domestic_map.setdefault(ent, []).append(hs)

    resonance: List[CrossTopic] = []
    domestic_only: List[CrossTopic] = []
    global_only: List[CrossTopic] = []

    all_entities = set(global_map) | set(domestic_map)
    for ent in all_entities:
        g_arts = global_map.get(ent, [])
        d_hot = domestic_map.get(ent, [])
        if g_arts and d_hot:
            resonance.append(
                CrossTopic(
                    keyword=ent,
                    hot_titles=[h.title for h in d_hot][:5],
                    global_titles=[a.title for a in g_arts][:5],
                    global_sources=sorted({a.source for a in g_arts if a.source}),
                )
            )
        elif d_hot and not g_arts:
            domestic_only.append(
                CrossTopic(keyword=ent, hot_titles=[h.title for h in d_hot][:5])
            )
        elif g_arts and not d_hot:
            global_only.append(
                CrossTopic(
                    keyword=ent,
                    global_titles=[a.title for a in g_arts][:5],
                    global_sources=sorted({a.source for a in g_arts if a.source}),
                )
            )

    resonance.sort(key=lambda t: (t.hot_count + t.global_count), reverse=True)
    domestic_only.sort(key=lambda t: t.hot_count, reverse=True)
    global_only.sort(key=lambda t: t.global_count, reverse=True)

    return CrossAnalysis(
        resonance=resonance[:max_per_bucket],
        domestic_only=domestic_only[:max_per_bucket],
        global_only=global_only[:max_per_bucket],
    )


def build_topic_packages(
    issue: DigestIssue,
    *,
    per_category: int = 12,
) -> Dict[str, List[DigestArticle]]:
    """按规则类目聚合文章（每类截断），作为渲染与 AI 摘要的压缩输入"""
    packages: Dict[str, List[DigestArticle]] = {}
    for art in issue.articles:
        cat = art.category or "综合"
        packages.setdefault(cat, []).append(art)
    for cat in packages:
        packages[cat] = packages[cat][:per_category]
    return packages


def build_ai_brief(
    issue: DigestIssue,
    hotspots: List[Hotspot],
    cross: CrossAnalysis,
    ai_config: Optional[Dict],
) -> str:
    """可选：调用 AI 生成「今日总判断」。AI 未配置或失败时返回空串。"""
    if not ai_config or not ai_config.get("API_KEY"):
        return ""
    try:
        from trendradar.ai.client import AIClient

        client = AIClient(ai_config)
        resonance_str = "；".join(
            f"{t.keyword}（国内{t.hot_count}/全球{t.global_count}）"
            for t in cross.resonance[:6]
        ) or "无明显共振"
        global_only_str = "、".join(t.keyword for t in cross.global_only[:6]) or "无"
        cat_str = "、".join(
            f"{k}:{v}" for k, v in sorted(issue.category_counts().items(), key=lambda x: -x[1])
        )
        prompt = (
            "你是一名资深国际时政与财经分析编辑，为中文付费读者撰写每日全球深度信号判断。\n"
            f"今日全球深度语料共 {issue.total_articles} 篇，类目分布：{cat_str}。\n"
            f"国内外同题共振：{resonance_str}。\n"
            f"全球重要但国内暂冷的主题：{global_only_str}。\n"
            "请用 2 到 4 句中文，给出当天最关键的一句话总判断，"
            "强调全球主线与国内关注的差异，不要罗列，不要编造来源。"
        )
        return client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=400,
        ).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"  [paid-daily] AI 总判断生成失败，跳过: {exc}")
        return ""


def build_theme_stories(
    issue: DigestIssue,
    ai_config: Optional[Dict],
    *,
    max_titles: int = 90,
    target_themes: int = 10,
) -> List[str]:
    """可选：用 AI 把零散文章归并为 8-12 个「主题故事」。

    仅喂中文标题 + 来源（不喂全文），控制 token；返回 Markdown 行列表，失败返回空。
    """
    if not ai_config or not ai_config.get("API_KEY"):
        return []
    try:
        from trendradar.ai.client import AIClient

        client = AIClient(ai_config)
        # 取每篇的「中文标题｜来源」，截断数量控制成本
        catalog = [
            f"{art.title_zh or art.title_en}｜{art.source}"
            for art in issue.articles[:max_titles]
            if (art.title_zh or art.title_en)
        ]
        prompt = (
            "下面是今日全球深度新闻的中文标题与来源清单（每行：标题｜来源）。\n"
            f"请归并为 {target_themes} 个左右的「主题故事」，每个主题用一行 Markdown：\n"
            "`- **主题名**：一句话概括该主题下的核心进展（标注主要来源）`。\n"
            "要求：聚焦重大主线，合并同类，不要逐条罗列，不要编造未出现的信息。\n\n"
            + "\n".join(catalog)
        )
        text = client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=1500,
        ).strip()
        lines = [ln.rstrip() for ln in text.splitlines() if ln.strip().startswith("-")]
        return lines
    except Exception as exc:  # noqa: BLE001
        print(f"  [paid-daily] AI 主题故事生成失败，跳过: {exc}")
        return []
