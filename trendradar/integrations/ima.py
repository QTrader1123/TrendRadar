# coding=utf-8
"""
IMA 知识库上传集成

复用 upload_to_ima_kb.py 脚本，在 AI 分析成功后上传 Markdown 报告。
"""

import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def resolve_ima_folder(
    template: str,
    md_path: Path,
    get_time_func=None,
) -> str:
    """解析 IMA 文件夹模板变量"""
    now = get_time_func() if get_time_func else datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    folder = (
        template.replace("{year}-{month}", now.strftime("%Y-%m"))
        .replace("{year}", now.strftime("%Y"))
        .replace("{month}", now.strftime("%m"))
        .replace("{date}", date_str)
        .replace("{today}", date_str)
    )
    return folder.strip("/\\")


def resolve_ima_filename(
    template: str,
    time_filename: str,
    period_name: Optional[str] = None,
) -> str:
    """解析 IMA 上传文件名模板"""
    safe_period = (period_name or "报告").replace("/", "-").replace("\\", "-")
    name = template.replace("{time}", time_filename).replace("{period_name}", safe_period)
    if not name.endswith(".md"):
        name += ".md"
    return name


def should_upload_to_ima(ai_result: Any, ima_config: Dict) -> bool:
    """方案二：仅 AI 分析成功时上传"""
    if not ima_config.get("ENABLED", False):
        return False
    if not ima_config.get("UPLOAD_ON_AI_SUCCESS", True):
        return False
    return bool(ai_result and getattr(ai_result, "success", False))


def upload_md_to_ima(
    md_path: str,
    ima_config: Dict,
    *,
    get_time_func=None,
    period_name: Optional[str] = None,
    time_filename: Optional[str] = None,
) -> bool:
    """
    上传 Markdown 报告到 IMA 知识库。

    Returns:
        True 上传成功，False 失败（不抛异常）
    """
    md_file = Path(md_path)
    if not md_file.exists():
        print(f"[IMA] 文件不存在，跳过上传: {md_path}")
        return False

    kb_name = ima_config.get("KB", "").strip()
    if not kb_name:
        print("[IMA] 未配置知识库名称 (ima.kb)，跳过上传")
        return False

    uploader_raw = ima_config.get("UPLOADER", "").strip()
    if not uploader_raw:
        print("[IMA] 未配置上传脚本 (ima.uploader)，跳过上传")
        return False

    uploader = Path(uploader_raw)
    if not uploader.is_absolute():
        uploader = (Path.cwd() / uploader).resolve()
    if not uploader.exists():
        print(f"[IMA] 上传脚本不存在: {uploader}")
        return False

    folder_name = resolve_ima_folder(ima_config.get("FOLDER", "{date}"), md_file, get_time_func)

    upload_path = md_file
    filename_template = ima_config.get("FILENAME", "")
    if filename_template and time_filename:
        target_name = resolve_ima_filename(filename_template, time_filename, period_name)
        staging_dir = md_file.parent / ".ima_upload"
        staging_dir.mkdir(parents=True, exist_ok=True)
        upload_path = staging_dir / target_name
        upload_path.write_text(md_file.read_text(encoding="utf-8"), encoding="utf-8")

    cmd = [
        sys.executable,
        str(uploader),
        str(upload_path),
        "--kb",
        kb_name,
        "--folder",
        folder_name,
    ]

    print(f"[IMA] 上传: {upload_path.name} → 「{kb_name}」/ {folder_name}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        print(f"[IMA] 上传失败: {exc}")
        return False

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            print(f"  [ima] {line}")
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        print(f"[IMA] 上传失败 (exit={result.returncode}): {err[:500]}")
        return False

    print("[IMA] 上传成功")
    return True
