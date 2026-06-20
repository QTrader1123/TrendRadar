# coding=utf-8
"""全球深度信号日报（B站充电专属）生成命令"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from trendradar.integrations.digest_parser import parse_digest_issue
from trendradar.integrations.digest_summarizer import (
    build_ai_brief,
    build_theme_stories,
    cross_analyze,
    load_hotspots,
    load_lexicon,
)
from trendradar.integrations.gmail_digest import load_latest_digest, load_latest_intro
from trendradar.integrations.ima import upload_md_to_ima
from trendradar.report.paid_daily import PRODUCT_NAME, save_paid_daily

# 记录“当日已生成成品”的标记，用于让定时任务在成品已产出后跳过 Gmail 查询
_STATE_FILE = Path("output/meta/paid_daily_state.json")


def _today_str(get_time_func=None) -> str:
    now = get_time_func() if get_time_func else datetime.now()
    return now.strftime("%Y-%m-%d")


def _mark_generated_today(get_time_func=None) -> None:
    """成品成功生成后写入当日标记"""
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(
            json.dumps({"last_generated_date": _today_str(get_time_func)}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def paid_daily_generated_today(get_time_func=None) -> bool:
    """当日是否已生成过成品（供主流程判断是否跳过 Gmail 查询）"""
    if not _STATE_FILE.exists():
        return False
    try:
        state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return state.get("last_generated_date") == _today_str(get_time_func)


def run_paid_daily(
    config: Dict,
    *,
    md_path: Optional[str] = None,
    intro_path: Optional[str] = None,
    use_ai: bool = False,
    skip_if_exists: bool = False,
    get_time_func=None,
) -> bool:
    """生成全球深度信号日报。

    Args:
        config: 全局配置
        md_path: 指定环球新闻深度日报 MD 路径；缺省取最新下载文件
        intro_path: 指定导读 HTML 路径；缺省自动匹配同期导读
        use_ai: 命令行强制启用 AI（与配置 use_ai_brief / ai_merge_topics 取或）
        skip_if_exists: 当日成品已存在则跳过（用于自动调度，避免重复生成）
    """
    digest_config = config.get("GMAIL_DIGEST", {})
    paid_config = config.get("PAID_DAILY", {})
    output_dir = digest_config.get("OUTPUT_DIR", "output/digest")
    output_root = paid_config.get("OUTPUT_DIR", "output/paid_daily")

    md = Path(md_path) if md_path else load_latest_digest(output_dir)
    if not md or not md.exists():
        print("❌ 未找到环球新闻深度日报 MD 文件，请先运行 --fetch-gmail-digest")
        return False

    intro = Path(intro_path) if intro_path else _guess_intro(md)

    print(f"[paid-daily] 解析全球语料: {md.name}")
    issue = parse_digest_issue(md, intro)
    print(
        f"  文章 {issue.total_articles} 篇 | 中文对齐 {issue.aligned_zh_count} | "
        f"质量 {issue.parse_quality}"
    )
    if intro and intro.exists():
        print(f"  导读: {intro.name}")

    if skip_if_exists:
        existing = Path(output_root) / (issue.date or "unknown") / f"{PRODUCT_NAME}-{issue.date}.md"
        if existing.exists():
            print(f"[paid-daily] 当日成品已存在，跳过生成: {existing}")
            return True

    print("[paid-daily] 读取国内热点...")
    hotspots = load_hotspots(config)
    print(f"  国内热点 {len(hotspots)} 条")

    lexicon = load_lexicon(paid_config.get("LEXICON_FILE"))
    cross = cross_analyze(issue, hotspots, lexicon=lexicon)
    print(
        f"  交叉分析: 同题共振 {len(cross.resonance)} | "
        f"内热外冷 {len(cross.domestic_only)} | 外热内冷 {len(cross.global_only)}"
    )

    ai_config = config.get("AI", {})
    ai_brief = ""
    if use_ai or paid_config.get("USE_AI_BRIEF", False):
        print("[paid-daily] 调用 AI 生成今日总判断...")
        ai_brief = build_ai_brief(issue, hotspots, cross, ai_config)

    theme_stories = []
    if use_ai or paid_config.get("AI_MERGE_TOPICS", False):
        print("[paid-daily] 调用 AI 归并主题故事...")
        theme_stories = build_theme_stories(issue, ai_config)
        if theme_stories:
            print(f"  生成主题故事 {len(theme_stories)} 条")

    paths = save_paid_daily(
        issue,
        hotspots,
        cross,
        ai_brief=ai_brief,
        theme_stories=theme_stories,
        output_root=output_root,
    )
    print("✅ 已生成全球深度信号日报:")
    for label, path in paths.items():
        print(f"   {label}: {path}")

    _mark_generated_today(get_time_func)
    _maybe_upload_ima(config, paid_config, paths)
    return True


def _maybe_upload_ima(config: Dict, paid_config: Dict, paths: Dict[str, str]) -> None:
    """按配置把基础日报与详细日报一并上传到 IMA 知识库"""
    if not paid_config.get("IMA_UPLOAD", False):
        return
    ima_config = dict(config.get("IMA", {}))
    if not ima_config.get("ENABLED", False):
        print("[paid-daily] IMA 未启用 (ima.enabled)，跳过上传")
        return
    # 本产品专属知识库与目录，不走「仅 AI 成功才上传」的限制
    paid_kb = paid_config.get("IMA_KB")
    if paid_kb:
        ima_config["KB"] = paid_kb
    # FOLDER 为空 → 直接上传到知识库根目录，不建子文件夹
    ima_config["FOLDER"] = paid_config.get("IMA_FOLDER", "")

    # 基础日报（充电成品版）+ 详细日报，按需上传
    targets = [
        ("基础日报", paths.get("product")),
        ("详细日报", paths.get("detailed")),
    ]
    for label, path in targets:
        if not path:
            continue
        print(f"[paid-daily] 上传{label}到 IMA...")
        upload_md_to_ima(path, ima_config)


def maybe_auto_generate(config: Dict, *, get_time_func=None) -> None:
    """主流程钩子：按配置在每次运行后自动生成全球深度信号日报。

    失败不抛异常，避免影响主分析流程。
    """
    paid_config = config.get("PAID_DAILY", {})
    if not paid_config.get("ENABLED", True):
        return
    if not paid_config.get("AUTO_GENERATE_ON_RUN", False):
        return
    try:
        run_paid_daily(
            config,
            skip_if_exists=paid_config.get("ONCE_PER_DAY", True),
            get_time_func=get_time_func,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[paid-daily] 自动生成失败（不影响主流程）: {exc}")


def _guess_intro(md_path: Path) -> Optional[Path]:
    """根据 MD 文件名推断同期导读 HTML"""
    candidate = md_path.parent / f"{md_path.stem}_导读.html"
    if candidate.exists():
        return candidate
    matches = sorted(md_path.parent.glob(f"{md_path.stem}_导读.*"))
    return matches[0] if matches else None
