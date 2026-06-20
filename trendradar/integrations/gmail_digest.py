# coding=utf-8
"""
Gmail 全球新闻日报附件下载

通过 IMAP 从 Gmail 收件箱拉取匹配邮件的附件，保存到本地供后续解析与报告融合。
"""

from __future__ import annotations

import email
import imaplib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


DEFAULT_EXTENSIONS = (".md", ".html", ".htm", ".txt", ".pdf", ".docx", ".doc")


@dataclass
class DownloadedAttachment:
    """已下载的附件信息"""

    message_id: str
    subject: str
    sender: str
    received_at: str
    filename: str
    saved_path: str
    size_bytes: int


@dataclass
class FetchResult:
    """拉取结果"""

    success: bool
    downloaded: List[DownloadedAttachment] = field(default_factory=list)
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    message: str = ""


def _decode_mime_header(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    chunks: List[str] = []
    for raw, charset in parts:
        if isinstance(raw, bytes):
            chunks.append(raw.decode(charset or "utf-8", errors="replace"))
        else:
            chunks.append(raw)
    return "".join(chunks).strip()


def _sanitize_filename(name: str) -> str:
    name = name.strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r'[<>:"|?*]', "_", name)
    return name or "attachment.bin"


def _match_filters(
    subject: str,
    sender: str,
    subject_contains: Sequence[str],
    sender_contains: Sequence[str],
) -> bool:
    if sender_contains:
        sender_lower = sender.lower()
        if not any(token.lower() in sender_lower for token in sender_contains if token):
            return False
    if subject_contains:
        subject_lower = subject.lower()
        if not any(token.lower() in subject_lower for token in subject_contains if token):
            return False
    return True


def _allowed_extension(filename: str, extensions: Sequence[str]) -> bool:
    if not extensions:
        return True
    lower = filename.lower()
    return any(lower.endswith(ext.lower()) for ext in extensions)


def _load_state(state_path: Path) -> Dict[str, Any]:
    if not state_path.exists():
        return {"processed_message_ids": []}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"processed_message_ids": []}


def _save_state(state_path: Path, state: Dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # 只保留最近 400 条，避免 state 无限增长
    ids = state.get("processed_message_ids", [])
    if len(ids) > 400:
        state["processed_message_ids"] = ids[-400:]
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_attachments(msg: Message, extensions: Sequence[str]) -> List[Tuple[str, bytes]]:
    found: List[Tuple[str, bytes]] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if not filename:
            continue
        if "attachment" not in disposition and "inline" not in disposition:
            continue
        filename = _decode_mime_header(filename)
        if not _allowed_extension(filename, extensions):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        found.append((filename, payload))
    return found


def _sort_attachments(
    attachments: List[Tuple[str, bytes]], prefer_md: bool
) -> List[Tuple[str, bytes]]:
    if not prefer_md:
        return attachments
    return sorted(
        attachments,
        key=lambda item: (0 if item[0].lower().endswith(".md") else 1, item[0].lower()),
    )


def _subject_base_name(subject: str) -> str:
    """从主题生成安全文件名，如 Hugin & Munin · 6月19日"""
    name = subject.strip() or "digest"
    name = name.replace("·", "_").replace("/", "-")
    return _sanitize_filename(name[:100])


def _extract_digest_date(
    subject: str,
    attachment_names: Sequence[str],
    msg: Message,
    now: datetime,
) -> str:
    """提取日报日期，优先使用附件名中的 YYYY-MM-DD"""
    for name in attachment_names:
        match = re.search(r"(20\d{2})[-_](\d{2})[-_](\d{2})", name)
        if match:
            return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    subject_match = re.search(r"(\d{1,2})月(\d{1,2})日", subject)
    if subject_match:
        month = int(subject_match.group(1))
        day = int(subject_match.group(2))
        return f"{now.year:04d}-{month:02d}-{day:02d}"

    received = _format_received(msg)
    received_match = re.search(r"(20\d{2})-(\d{2})-(\d{2})", received)
    if received_match:
        return received_match.group(0)

    return now.strftime("%Y-%m-%d")


def _render_filename_template(template: str, *, date_str: str, subject: str, original_name: str = "") -> str:
    """渲染文件名模板，避免沿用来源附件品牌名"""
    subject_name = _subject_base_name(subject)
    rendered = (
        template.replace("{date}", date_str)
        .replace("{subject}", subject_name)
        .replace("{original}", _sanitize_filename(original_name))
    )
    return _sanitize_filename(rendered)


def _build_imap_query(
    lookback_days: int,
    subject_contains: Sequence[str],
    sender_contains: Sequence[str],
) -> str:
    since = (datetime.now() - timedelta(days=max(lookback_days, 1))).strftime("%d-%b-%Y")
    clauses = [f'SINCE "{since}"']
    if sender_contains:
        # Gmail IMAP 支持 FROM；多个发件人取第一个精确匹配，其余在本地过滤
        clauses.append(f'FROM "{sender_contains[0]}"')
    if subject_contains:
        clauses.append(f'SUBJECT "{subject_contains[0]}"')
    return " ".join(clauses)


def fetch_gmail_digest(config: Dict[str, Any], *, get_time_func=None) -> FetchResult:
    """
    从 Gmail IMAP 下载全球新闻日报附件。

    Args:
        config: GMAIL_DIGEST 配置块（来自 loader）
        get_time_func: 可选时间函数，用于输出目录日期

    Returns:
        FetchResult
    """
    if not config.get("ENABLED", False):
        return FetchResult(success=False, message="gmail_digest 未启用")

    user = config.get("USER", "").strip()
    password = config.get("PASSWORD", "").strip()
    if not user or not password:
        return FetchResult(success=False, message="未配置 Gmail 账号或应用专用密码")

    imap_server = config.get("IMAP_SERVER", "imap.gmail.com")
    imap_port = int(config.get("IMAP_PORT", 993))
    folder = config.get("FOLDER", "INBOX")
    output_dir = Path(config.get("OUTPUT_DIR", "output/digest"))
    state_path = Path(config.get("STATE_FILE", "output/meta/gmail_digest_state.json"))
    extensions = tuple(config.get("ATTACHMENT_EXTENSIONS") or DEFAULT_EXTENSIONS)
    subject_contains = config.get("SUBJECT_CONTAINS") or []
    sender_contains = config.get("SENDER_CONTAINS") or []
    lookback_days = int(config.get("LOOKBACK_DAYS", 3))
    mark_as_read = bool(config.get("MARK_AS_READ", False))
    save_email_body = bool(config.get("SAVE_EMAIL_BODY", True))
    prefer_md = bool(config.get("PREFER_MD_ATTACHMENT", True))
    source_name = config.get("SOURCE_NAME", "全球新闻日报")
    attachment_filename_template = config.get("ATTACHMENT_FILENAME", "hugin-munin-daily-{date}.md")
    intro_filename_template = config.get("INTRO_FILENAME", "hugin-munin-daily-{date}_intro.html")

    now = get_time_func() if get_time_func else datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    day_dir = output_dir / date_str
    day_dir.mkdir(parents=True, exist_ok=True)

    state = _load_state(state_path)
    processed: set = set(state.get("processed_message_ids", []))

    downloaded: List[DownloadedAttachment] = []
    errors: List[str] = []
    skipped = 0

    mail: Optional[imaplib.IMAP4_SSL] = None
    try:
        print(f"[Gmail] 拉取 {source_name} ({user})")
        print(f"[Gmail] 连接 {imap_server}:{imap_port} ...")
        mail = imaplib.IMAP4_SSL(imap_server, imap_port)
        mail.login(user, password)
        status, _ = mail.select(folder)
        if status != "OK":
            return FetchResult(success=False, message=f"无法打开邮箱文件夹: {folder}")

        query = _build_imap_query(lookback_days, subject_contains, sender_contains)
        print(f"[Gmail] 搜索: {query}")
        status, data = mail.search(None, query)
        if status != "OK":
            return FetchResult(success=False, message="IMAP 搜索失败")

        msg_ids = data[0].split() if data and data[0] else []
        if not msg_ids:
            print("[Gmail] 未找到匹配邮件")
            return FetchResult(success=True, message="未找到匹配邮件", downloaded=[])

        # 从旧到新处理，确保最新邮件最后写入
        for num in msg_ids:
            status, msg_data = mail.fetch(num, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                errors.append(f"fetch 失败: {num!r}")
                continue

            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            message_id = (msg.get("Message-ID") or f"uid-{num.decode()}").strip()
            subject = _decode_mime_header(msg.get("Subject"))
            sender = _decode_mime_header(msg.get("From"))

            if message_id in processed:
                skipped += 1
                continue

            if not _match_filters(subject, sender, subject_contains, sender_contains):
                skipped += 1
                continue

            attachments = _sort_attachments(_extract_attachments(msg, extensions), prefer_md)
            message_saved = False
            digest_date = _extract_digest_date(subject, [name for name, _ in attachments], msg, now)
            base_name = Path(
                _render_filename_template(
                    intro_filename_template,
                    date_str=digest_date,
                    subject=subject,
                )
            ).stem
            has_md = any(name.lower().endswith(".md") for name, _ in attachments)

            if save_email_body and (not attachments or has_md):
                # Hugin & Munin：正文含简介+目录，MD 附件含全文；两者都保留
                body_text, body_html = _extract_body(msg)
                intro_name = _render_filename_template(
                    intro_filename_template if has_md else "{subject}.html",
                    date_str=digest_date,
                    subject=subject,
                )
                if body_html:
                    path = day_dir / intro_name
                    path.write_text(body_html, encoding="utf-8")
                    downloaded.append(
                        DownloadedAttachment(
                            message_id=message_id,
                            subject=subject,
                            sender=sender,
                            received_at=_format_received(msg),
                            filename=path.name,
                            saved_path=str(path),
                            size_bytes=path.stat().st_size,
                        )
                    )
                    message_saved = True
                elif body_text and not attachments:
                    path = day_dir / f"{base_name}.txt"
                    path.write_text(body_text, encoding="utf-8")
                    downloaded.append(
                        DownloadedAttachment(
                            message_id=message_id,
                            subject=subject,
                            sender=sender,
                            received_at=_format_received(msg),
                            filename=path.name,
                            saved_path=str(path),
                            size_bytes=path.stat().st_size,
                        )
                    )
                    message_saved = True

            elif not attachments and save_email_body:
                # 无附件时仅保存正文（兼容其他日报源）
                body_text, body_html = _extract_body(msg)
                if body_html or body_text:
                    if body_html:
                        path = day_dir / f"{base_name}.html"
                        path.write_text(body_html, encoding="utf-8")
                    else:
                        path = day_dir / f"{base_name}.txt"
                        path.write_text(body_text, encoding="utf-8")
                    downloaded.append(
                        DownloadedAttachment(
                            message_id=message_id,
                            subject=subject,
                            sender=sender,
                            received_at=_format_received(msg),
                            filename=path.name,
                            saved_path=str(path),
                            size_bytes=path.stat().st_size,
                        )
                    )
                    message_saved = True

            for filename, payload in attachments:
                if filename.lower().endswith(".md"):
                    safe_name = _render_filename_template(
                        attachment_filename_template,
                        date_str=digest_date,
                        subject=subject,
                        original_name=filename,
                    )
                else:
                    safe_name = _sanitize_filename(filename)
                target = day_dir / safe_name
                if target.exists():
                    stem, suffix = target.stem, target.suffix
                    target = day_dir / f"{stem}_{num.decode()}{suffix}"
                target.write_bytes(payload)
                downloaded.append(
                    DownloadedAttachment(
                        message_id=message_id,
                        subject=subject,
                        sender=sender,
                        received_at=_format_received(msg),
                        filename=target.name,
                        saved_path=str(target),
                        size_bytes=len(payload),
                    )
                )
                message_saved = True

            if message_saved:
                processed.add(message_id)
                if mark_as_read:
                    mail.store(num, "+FLAGS", "\\Seen")

        if downloaded:
            state["processed_message_ids"] = list(processed)
            state["last_fetch_at"] = now.isoformat(timespec="seconds")
            state["last_download_count"] = len(downloaded)
            _save_state(state_path, state)
            print(f"[Gmail] 已下载 {len(downloaded)} 个文件 → {day_dir}")
            for item in downloaded:
                print(f"  • {item.filename} ({item.size_bytes} bytes)")
        else:
            print(f"[Gmail] 无新附件（跳过 {skipped} 封已处理/不匹配邮件）")

        return FetchResult(
            success=True,
            downloaded=downloaded,
            skipped=skipped,
            errors=errors,
            message=f"下载 {len(downloaded)} 个文件",
        )

    except imaplib.IMAP4.error as exc:
        return FetchResult(success=False, message=f"IMAP 错误: {exc}", errors=[str(exc)])
    except OSError as exc:
        return FetchResult(success=False, message=f"网络/IO 错误: {exc}", errors=[str(exc)])
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass


def _format_received(msg: Message) -> str:
    date_hdr = msg.get("Date")
    if not date_hdr:
        return ""
    try:
        return parsedate_to_datetime(date_hdr).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError):
        return date_hdr


def _extract_body(msg: Message) -> Tuple[str, str]:
    text_parts: List[str] = []
    html_parts: List[str] = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        ctype = part.get_content_type()
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            content = payload.decode(charset, errors="replace")
        except LookupError:
            content = payload.decode("utf-8", errors="replace")
        if ctype == "text/plain":
            text_parts.append(content)
        elif ctype == "text/html":
            html_parts.append(content)
    return "\n".join(text_parts).strip(), "\n".join(html_parts).strip()


def load_latest_digest(output_dir: str = "output/digest") -> Optional[Path]:
    """返回最新一天目录下的 MD 附件（优先）或最近文件"""
    root = Path(output_dir)
    if not root.exists():
        return None
    date_dirs = sorted([p for p in root.iterdir() if p.is_dir() and p.name != "research"], reverse=True)
    skip_names = {"samples"}

    for day in date_dirs:
        if day.name in skip_names:
            continue
        md_files = [
            f for f in day.glob("*.md")
            if not f.name.endswith("_preview.md") and not f.name.endswith("_research.md")
        ]
        if md_files:
            return sorted(md_files, key=lambda p: p.stat().st_mtime, reverse=True)[0]
        others = [
            f for f in day.iterdir()
            if f.is_file() and not f.name.endswith("_intro.html")
        ]
        if others:
            return sorted(others, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None


def load_latest_intro(output_dir: str = "output/digest") -> Optional[Path]:
    """返回最新邮件正文简介（*_intro.html）"""
    root = Path(output_dir)
    if not root.exists():
        return None
    date_dirs = sorted([p for p in root.iterdir() if p.is_dir() and p.name != "research"], reverse=True)
    for day in date_dirs:
        intros = list(day.glob("*_intro.html"))
        if intros:
            return sorted(intros, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    return None
