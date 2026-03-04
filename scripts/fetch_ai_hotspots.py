#!/usr/bin/env python3
"""Fetch daily AI hot topics from Chinese media RSS feeds."""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable


DEFAULT_SOURCES: list[tuple[str, str]] = [
    ("36氪", "https://36kr.com/feed"),
    ("爱范儿", "https://www.ifanr.com/feed"),
    ("雷峰网", "https://www.leiphone.com/feed"),
    ("IT之家", "https://www.ithome.com/rss/"),
    ("虎嗅", "https://www.huxiu.com/rss/0.xml"),
]

CN_AI_KEYWORDS = [
    "人工智能",
    "大模型",
    "多模态",
    "机器学习",
    "生成式",
    "智能体",
    "语言模型",
    "具身智能",
    "计算机视觉",
    "自动驾驶",
    "文心",
    "通义",
    "智谱",
    "豆包",
    "元宝",
]

EN_AI_KEYWORDS = [
    "ai",
    "aigc",
    "llm",
    "agent",
    "gpt",
    "openai",
    "claude",
    "gemini",
    "llama",
    "deepseek",
    "kimi",
    "copilot",
]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    )
}


@dataclass
class FeedItem:
    source: str
    title: str
    link: str
    summary: str
    published_at: datetime | None


def fetch_feed_xml(url: str, timeout: int = 15) -> str:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", None)
        if isinstance(reason, ssl.SSLCertVerificationError):
            print(f"[WARN] {url} 证书校验失败，已使用不校验证书模式重试。")
            insecure_ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(request, timeout=timeout, context=insecure_ctx) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(charset, errors="replace")
        raise


def clean_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def sanitize_xml(xml_text: str) -> str:
    sanitized = re.sub(
        r"[^\x09\x0A\x0D\x20-\uD7FF\uE000-\uFFFD]",
        "",
        xml_text,
    )
    return re.sub(r"&(?![A-Za-z]+;|#\d+;|#x[0-9A-Fa-f]+;)", "&amp;", sanitized)


def parse_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    stripped = text.strip()
    try:
        dt = parsedate_to_datetime(stripped)
        if dt and dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        if dt:
            return dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass

    iso_candidate = stripped.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_candidate)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_rss_items(root: ET.Element, source: str) -> list[FeedItem]:
    items: list[FeedItem] = []
    for item in root.findall(".//channel/item"):
        title = clean_text(item.findtext("title"))
        link = clean_text(item.findtext("link"))
        summary = clean_text(item.findtext("description") or item.findtext("{*}encoded"))
        pub_text = item.findtext("pubDate") or item.findtext("{*}published")
        if title and link:
            items.append(
                FeedItem(
                    source=source,
                    title=title,
                    link=link,
                    summary=summary,
                    published_at=parse_datetime(pub_text),
                )
            )
    return items


def parse_atom_items(root: ET.Element, source: str) -> list[FeedItem]:
    items: list[FeedItem] = []
    for entry in root.findall(".//{*}entry"):
        title = clean_text(entry.findtext("{*}title"))

        link = ""
        for link_elem in entry.findall("{*}link"):
            href = link_elem.attrib.get("href", "").strip()
            if href:
                link = href
                break

        summary = clean_text(entry.findtext("{*}summary") or entry.findtext("{*}content"))
        pub_text = (
            entry.findtext("{*}published")
            or entry.findtext("{*}updated")
            or entry.findtext("{*}created")
        )

        if title and link:
            items.append(
                FeedItem(
                    source=source,
                    title=title,
                    link=link,
                    summary=summary,
                    published_at=parse_datetime(pub_text),
                )
            )
    return items


def parse_feed(source: str, xml_text: str) -> list[FeedItem]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        root = ET.fromstring(sanitize_xml(xml_text))
    rss_items = parse_rss_items(root, source)
    if rss_items:
        return rss_items
    return parse_atom_items(root, source)


def contains_ai_keyword(text: str, keywords: Iterable[str]) -> bool:
    lower_text = text.lower()
    for keyword in keywords:
        if keyword in lower_text:
            return True

    for keyword in EN_AI_KEYWORDS:
        pattern = rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])"
        if re.search(pattern, lower_text):
            return True
    return False


def sort_key(item: FeedItem) -> datetime:
    return item.published_at or datetime.min.replace(tzinfo=timezone.utc)


def format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "未知时间"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def save_markdown(items: list[FeedItem], output_dir: Path, run_at: datetime) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"ai_hotspots_{run_at.strftime('%Y-%m-%d')}.md"
    out_file = output_dir / file_name

    lines = [
        "# AI 热点日报",
        "",
        f"- 生成时间: {run_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 条目数量: {len(items)}",
        "",
    ]

    if not items:
        lines.append("今日未抓取到符合关键词的内容。")
    else:
        for idx, item in enumerate(items, start=1):
            lines.append(f"## {idx}. {item.title}")
            lines.append(f"- 来源: {item.source}")
            lines.append(f"- 时间: {format_datetime(item.published_at)}")
            lines.append(f"- 链接: {item.link}")
            if item.summary:
                lines.append(f"- 摘要: {item.summary[:180]}{'...' if len(item.summary) > 180 else ''}")
            lines.append("")

    out_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    latest_file = output_dir / "latest_ai_hotspots.md"
    latest_file.write_text(out_file.read_text(encoding="utf-8"), encoding="utf-8")
    return out_file


def item_to_json_dict(item: FeedItem) -> dict[str, str | None]:
    return {
        "source": item.source,
        "title": item.title,
        "link": item.link,
        "summary": item.summary[:180] + ("..." if len(item.summary) > 180 else ""),
        "published_at_utc": item.published_at.isoformat() if item.published_at else None,
        "published_at_local": format_datetime(item.published_at),
    }


def save_site_json(items: list[FeedItem], site_dir: Path, run_at: datetime) -> Path:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    local_date = run_at.astimezone().strftime("%Y-%m-%d")
    payload = {
        "generated_at_utc": run_at.isoformat(),
        "generated_at_local": run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "count": len(items),
        "sources": [{"name": name, "url": url} for name, url in DEFAULT_SOURCES],
        "items": [item_to_json_dict(item) for item in items],
    }

    dated_file = data_dir / f"ai_hotspots_{local_date}.json"
    latest_file = data_dir / "latest.json"
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    dated_file.write_text(content, encoding="utf-8")
    latest_file.write_text(content, encoding="utf-8")
    return latest_file


def build_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取中文媒体 AI 热点信息。")
    parser.add_argument("--limit", type=int, default=10, help="输出条数，默认 10")
    parser.add_argument(
        "--days",
        type=int,
        default=3,
        help="仅保留最近 N 天内容，默认 3",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="输出目录，默认 output",
    )
    parser.add_argument(
        "--site-dir",
        default="site",
        help="网站目录，默认 site（会写入 data/latest.json）",
    )
    return parser.parse_args()


def select_with_source_balance(items: list[FeedItem], limit: int) -> list[FeedItem]:
    if not items:
        return []
    sorted_items = sorted(items, key=sort_key, reverse=True)
    per_source_cap = max(2, limit // max(len(DEFAULT_SOURCES), 1) + 1)

    selected: list[FeedItem] = []
    source_counter: dict[str, int] = {}

    for item in sorted_items:
        if len(selected) >= limit:
            break
        count = source_counter.get(item.source, 0)
        if count >= per_source_cap:
            continue
        selected.append(item)
        source_counter[item.source] = count + 1

    if len(selected) < limit:
        selected_links = {item.link for item in selected}
        for item in sorted_items:
            if len(selected) >= limit:
                break
            if item.link in selected_links:
                continue
            selected.append(item)
            selected_links.add(item.link)

    return selected


def main() -> int:
    args = build_args()
    run_at = datetime.now(timezone.utc)
    earliest = run_at - timedelta(days=max(args.days, 1))
    all_items: list[FeedItem] = []

    for source_name, source_url in DEFAULT_SOURCES:
        try:
            xml_text = fetch_feed_xml(source_url)
            parsed_items = parse_feed(source_name, xml_text)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            ET.ParseError,
            UnicodeDecodeError,
        ) as error:
            print(f"[WARN] {source_name} 抓取失败: {error}")
            continue

        for item in parsed_items:
            combined_text = f"{item.title} {item.summary}"
            if not contains_ai_keyword(combined_text, CN_AI_KEYWORDS):
                continue
            if item.published_at and item.published_at < earliest:
                continue
            all_items.append(item)

    dedup: dict[str, FeedItem] = {}
    for item in all_items:
        key = item.link.strip().lower() or item.title.strip().lower()
        existing = dedup.get(key)
        if not existing or sort_key(item) > sort_key(existing):
            dedup[key] = item

    selected = select_with_source_balance(list(dedup.values()), max(args.limit, 1))
    output_path = save_markdown(selected, Path(args.output_dir), run_at)
    site_json = save_site_json(selected, Path(args.site_dir), run_at)
    print(f"[OK] 已生成 {len(selected)} 条: {output_path}")
    print(f"[OK] 网站数据已更新: {site_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
