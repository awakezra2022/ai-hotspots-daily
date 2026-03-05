#!/usr/bin/env python3
"""Fetch daily AI hot topics from Chinese media RSS feeds."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import unicodedata
import urllib.error
import urllib.parse
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
    "千问",
    "qwen",
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
    "qwen",
]

HOT_KEYWORD_WEIGHTS: dict[str, int] = {
    "发布": 2,
    "上线": 2,
    "开源": 3,
    "融资": 3,
    "财报": 3,
    "并购": 3,
    "芯片": 3,
    "算力": 3,
    "模型": 3,
    "agent": 3,
    "智能体": 3,
    "爆火": 4,
    "热搜": 4,
    "突发": 4,
    "mwc": 2,
    "架构": 3,
    "调整": 3,
    "离职": 3,
}

TREND_TERMS: list[str] = [
    "openai",
    "deepseek",
    "anthropic",
    "claude",
    "gemini",
    "gpt",
    "llama",
    "copilot",
    "英伟达",
    "nvidia",
    "特斯拉",
    "小米",
    "阿里",
    "字节",
    "腾讯",
    "华为",
    "苹果",
    "机器人",
    "自动驾驶",
    "qwen",
    "千问",
]

EARLY_BRIEFING_TERMS = {
    "早报",
    "晚报",
    "日报",
    "午报",
    "导语",
    "要闻",
    "速览",
    "汇总",
    "8点1氪",
    "36氪8点1氪",
}

EVENT_GENERIC_TERMS = {
    "公司",
    "行业",
    "技术",
    "产品",
    "平台",
    "报道",
    "消息",
    "最新",
    "今日",
    "宣布",
    "发布",
    "上线",
    "模型",
    "ai",
    "ceo",
    "成立",
    "支持",
    "小组",
    "加大",
    "研发",
    "投入",
    "市场",
    "平台",
    "体验",
    "应用",
}

EVENT_ALIASES: dict[str, list[str]] = {
    "qwen": ["qwen", "千问", "通义千问", "阿里千问", "qwen2", "qwen3"],
    "openai": ["openai", "chatgpt", "gpt"],
    "deepseek": ["deepseek", "深度求索"],
    "anthropic": ["anthropic", "claude"],
    "gemini": ["gemini", "谷歌大模型"],
    "nvidia": ["nvidia", "英伟达"],
    "bytedance": ["字节", "豆包"],
    "alibaba": ["阿里", "通义"],
    "tencent": ["腾讯", "混元"],
    "baidu": ["百度", "文心"],
}

ACTION_ALIASES: dict[str, list[str]] = {
    "组织变动": ["离职", "辞职", "卸任", "调整", "重组", "架构", "变动", "大地震"],
    "发布": ["发布", "上线", "开源"],
    "投融资": ["融资", "并购", "收购"],
}

WECHAT_ACCOUNTS = ["机器之心", "量子位", "新智元"]
WECHAT_QUERY_TERMS = ["AI", "大模型", "千问", "Qwen", "OpenAI", "DeepSeek"]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


@dataclass
class FeedItem:
    source: str
    title: str
    link: str
    summary: str
    published_at: datetime | None


@dataclass
class SocialHit:
    channel: str
    provider: str
    text: str
    published_at: datetime | None


@dataclass
class ChannelStatus:
    channel: str
    provider: str
    status: str
    message: str


@dataclass
class EventProfile:
    event_id: str
    signature_tokens: list[str]
    matched_channels: list[str]
    evidence_count: int
    sample_evidence: str
    latest_hit_at: datetime | None
    verified: bool
    social_heat_score: int


@dataclass
class RankedItem:
    item: FeedItem
    event_id: str
    media_hotness_score: int
    recency_score: float
    social_heat_score: int
    verification_bonus: int
    final_score: float
    verification_channels: list[str]
    evidence_count: int
    sample_evidence: str


@dataclass
class SocialBundle:
    hits: list[SocialHit]
    status: str
    failures: list[str]
    channels: list[ChannelStatus]


def fetch_text(url: str, timeout: int = 15) -> str:
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


def fetch_json(url: str, timeout: int = 15) -> dict | list:
    text = fetch_text(url, timeout=timeout)
    return json.loads(text)


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

    custom = re.sub(r"\s+", " ", stripped)
    for pattern, fmt in (
        (r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4}$", "%Y-%m-%d %H:%M:%S %z"),
        (r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", "%Y-%m-%d %H:%M:%S"),
    ):
        if re.match(pattern, custom):
            try:
                dt = datetime.strptime(custom, fmt)
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                pass

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


def title_text(item: FeedItem) -> str:
    return item.title.lower()


def extract_trend_terms(text: str) -> set[str]:
    return {term for term in TREND_TERMS if term in text}


def build_trend_frequency(items: list[FeedItem]) -> dict[str, int]:
    freq: dict[str, int] = {}
    for item in items:
        terms = extract_trend_terms(title_text(item))
        for term in terms:
            freq[term] = freq.get(term, 0) + 1
    return freq


def compute_media_hotness_score(item: FeedItem, trend_freq: dict[str, int], event_size: int) -> int:
    text = title_text(item)
    score = 0

    for keyword, weight in HOT_KEYWORD_WEIGHTS.items():
        if keyword in text:
            score += weight

    for term in extract_trend_terms(text):
        if trend_freq.get(term, 0) > 1:
            score += min(4, trend_freq[term] - 1)

    if event_size > 1:
        score += min(8, (event_size - 1) * 2)

    return min(score, 30)


def compute_recency_score(published_at: datetime | None, run_at: datetime) -> float:
    if published_at is None:
        return 0.0
    hours_ago = max((run_at - published_at).total_seconds() / 3600.0, 0.0)
    return max(0.0, 100.0 - min(72.0, hours_ago) * (100.0 / 72.0))


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title).lower()
    text = re.sub(r"\d+点1氪", " ", text)
    for term in EARLY_BRIEFING_TERMS:
        text = text.replace(term, " ")
    text = re.sub(r"[^\u4e00-\u9fff0-9a-z]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_tokens(normalized: str) -> list[str]:
    tokens: list[str] = []

    for canonical, aliases in EVENT_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            tokens.append(canonical)

    for canonical, aliases in ACTION_ALIASES.items():
        if any(alias in normalized for alias in aliases):
            tokens.append(canonical)

    en_tokens = re.findall(r"[a-z][a-z0-9+.-]{1,}", normalized)
    zh_tokens = re.findall(r"[\u4e00-\u9fff]{2,8}", normalized)

    for token in en_tokens + zh_tokens:
        if token in EVENT_GENERIC_TERMS:
            continue
        if token in EARLY_BRIEFING_TERMS:
            continue
        if token in tokens:
            continue
        tokens.append(token)

    return tokens[:8]


def build_event_id(title: str, summary: str = "") -> tuple[str, list[str]]:
    normalized = normalize_title(f"{title} {summary[:120]}")
    tokens = canonical_tokens(normalized)
    if not tokens:
        fallback = normalized[:24] or "untitled"
        tokens = [fallback]
    subject_hits = [token for token in tokens if token in EVENT_ALIASES]
    action_hits = [token for token in tokens if token in ACTION_ALIASES]
    extra_hits = [token for token in tokens if token not in subject_hits and token not in action_hits]
    raw_person_hits = re.findall(r"([\u4e00-\u9fff]{2,8})(?:离职|辞职|卸任|加入)", normalized)
    person_hits: list[str] = []
    for candidate in raw_person_hits:
        clean_candidate = candidate
        for prefix in ("技术负责人", "负责人", "回应", "批准", "同学", "公司", "团队", "核心"):
            if clean_candidate.startswith(prefix):
                clean_candidate = clean_candidate[len(prefix) :]
        if len(clean_candidate) > 4:
            clean_candidate = clean_candidate[-3:]
        if 2 <= len(clean_candidate) <= 4:
            person_hits.append(clean_candidate)

    signature_tokens: list[str] = []
    if person_hits and "组织变动" in action_hits:
        signature_tokens.extend([person_hits[0], "组织变动"])
    else:
        if subject_hits:
            signature_tokens.append(subject_hits[0])
        if action_hits:
            signature_tokens.append(action_hits[0])
    if not signature_tokens and extra_hits:
        signature_tokens.extend(extra_hits[:2])

    if not signature_tokens:
        signature_tokens = tokens[:2]

    signature = "|".join(sorted(dict.fromkeys(signature_tokens)))
    event_id = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
    match_tokens = list(dict.fromkeys(signature_tokens + extra_hits))[:8]
    return event_id, match_tokens


def compute_final_score(
    recency_score: float,
    media_hotness_score: int,
    social_heat_score: int,
    verification_bonus: int,
) -> float:
    return round(recency_score + media_hotness_score + social_heat_score + verification_bonus, 2)


def extract_weibo_items_from_json(payload: dict | list) -> list[str]:
    texts: list[str] = []
    candidates: list[dict] = []

    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("realtime", "hotgov", "band_list"):
                value = data.get(key)
                if isinstance(value, list):
                    candidates.extend(entry for entry in value if isinstance(entry, dict))
        for key in ("realtime", "band_list"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(entry for entry in value if isinstance(entry, dict))

    for entry in candidates:
        title = clean_text(
            str(
                entry.get("word")
                or entry.get("note")
                or entry.get("title")
                or entry.get("topic")
                or ""
            )
        )
        if title:
            texts.append(title)
    return texts


def fetch_weibo_signals() -> tuple[list[SocialHit], list[ChannelStatus], list[str]]:
    hits: list[SocialHit] = []
    statuses: list[ChannelStatus] = []
    failures: list[str] = []

    official_urls = [
        "https://weibo.com/ajax/side/hotSearch",
        "https://weibo.com/ajax/statuses/hot_band",
    ]

    official_error: str | None = None
    for url in official_urls:
        try:
            payload = fetch_json(url, timeout=12)
            titles = extract_weibo_items_from_json(payload)
            if titles:
                for title in titles:
                    hits.append(
                        SocialHit(
                            channel="weibo",
                            provider="official",
                            text=title,
                            published_at=None,
                        )
                    )
                statuses.append(
                    ChannelStatus(
                        channel="weibo",
                        provider="official",
                        status="ok",
                        message=f"官方源命中 {len(titles)} 条",
                    )
                )
                return hits, statuses, failures
            official_error = "官方返回为空"
        except Exception as error:  # noqa: BLE001
            official_error = str(error)

    fallback_url = "https://s.weibo.com/top/summary?cate=realtimehot"
    try:
        html_text = fetch_text(fallback_url, timeout=12)
        titles = [clean_text(m) for m in re.findall(r"<a[^>]*>([^<]{4,80})</a>", html_text)]
        titles = [title for title in titles if contains_ai_keyword(title, CN_AI_KEYWORDS)]
        unique_titles = list(dict.fromkeys(titles))[:80]
        if unique_titles:
            for title in unique_titles:
                hits.append(
                    SocialHit(
                        channel="weibo",
                        provider="fallback",
                        text=title,
                        published_at=None,
                    )
                )
            statuses.append(
                ChannelStatus(
                    channel="weibo",
                    provider="fallback",
                    status="degraded",
                    message=f"官方受限({official_error or 'unknown'})，已回退公开页命中 {len(unique_titles)} 条",
                )
            )
            return hits, statuses, failures
        failures.append(f"weibo: 官方失败({official_error or 'unknown'}), 回退源无数据")
        statuses.append(
            ChannelStatus(
                channel="weibo",
                provider="fallback",
                status="error",
                message="官方与回退源均无可用数据",
            )
        )
    except Exception as error:  # noqa: BLE001
        failures.append(f"weibo: 官方失败({official_error or 'unknown'}), 回退失败({error})")
        statuses.append(
            ChannelStatus(
                channel="weibo",
                provider="fallback",
                status="error",
                message=f"官方失败，回退失败: {error}",
            )
        )

    return hits, statuses, failures


def fetch_zhihu_signals() -> tuple[list[SocialHit], list[ChannelStatus], list[str]]:
    hits: list[SocialHit] = []
    statuses: list[ChannelStatus] = []
    failures: list[str] = []

    official_url = "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50&desktop=true"
    official_error: str | None = None

    try:
        payload = fetch_json(official_url, timeout=12)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        titles: list[str] = []
        for row in data:
            if not isinstance(row, dict):
                continue
            target = row.get("target", {})
            if isinstance(target, dict):
                title = clean_text(str(target.get("title") or target.get("excerpt") or ""))
                if title:
                    titles.append(title)
        if titles:
            for title in titles:
                hits.append(
                    SocialHit(channel="zhihu", provider="official", text=title, published_at=None)
                )
            statuses.append(
                ChannelStatus(
                    channel="zhihu",
                    provider="official",
                    status="ok",
                    message=f"官方源命中 {len(titles)} 条",
                )
            )
            return hits, statuses, failures
        official_error = "官方返回为空"
    except Exception as error:  # noqa: BLE001
        official_error = str(error)

    fallback_url = "https://rsshub.app/zhihu/hotlist"
    try:
        xml_text = fetch_text(fallback_url, timeout=12)
        entries = parse_feed("知乎热榜代理", xml_text)
        titles = [entry.title for entry in entries if entry.title]
        if titles:
            for title in titles:
                hits.append(
                    SocialHit(channel="zhihu", provider="fallback", text=title, published_at=None)
                )
            statuses.append(
                ChannelStatus(
                    channel="zhihu",
                    provider="fallback",
                    status="degraded",
                    message=f"官方受限({official_error or 'unknown'})，已回退代理命中 {len(titles)} 条",
                )
            )
            return hits, statuses, failures
        failures.append(f"zhihu: 官方失败({official_error or 'unknown'}), 回退源无数据")
        statuses.append(
            ChannelStatus(
                channel="zhihu",
                provider="fallback",
                status="error",
                message="官方与回退源均无可用数据",
            )
        )
    except Exception as error:  # noqa: BLE001
        failures.append(f"zhihu: 官方失败({official_error or 'unknown'}), 回退失败({error})")
        statuses.append(
            ChannelStatus(
                channel="zhihu",
                provider="fallback",
                status="error",
                message=f"官方失败，回退失败: {error}",
            )
        )

    return hits, statuses, failures


def fetch_wechat_signals(accounts: list[str]) -> tuple[list[SocialHit], list[ChannelStatus], list[str]]:
    hits: list[SocialHit] = []
    statuses: list[ChannelStatus] = []
    failures: list[str] = []

    for account in accounts:
        account_hit_keys: set[str] = set()
        account_hit_count = 0
        account_errors: list[str] = []

        for term in WECHAT_QUERY_TERMS:
            query = urllib.parse.quote(f"{account} {term}")
            url = f"https://weixin.sogou.com/weixin?type=2&query={query}&ie=utf8"
            try:
                page = fetch_text(url, timeout=12)
                title_matches = re.findall(
                    r"id=\"sogou_vr_11002601_title_[^\"]+\"[^>]*>(.*?)</a>",
                    page,
                    flags=re.IGNORECASE | re.DOTALL,
                )
                snippet_matches = re.findall(
                    r"class=\"txt-info\"[^>]*>(.*?)</p>",
                    page,
                    flags=re.IGNORECASE | re.DOTALL,
                )

                for idx, raw_title in enumerate(title_matches[:10]):
                    title = clean_text(raw_title)
                    snippet = clean_text(snippet_matches[idx] if idx < len(snippet_matches) else "")
                    combined = f"{account} {term} {title} {snippet}".strip()
                    if not combined:
                        continue
                    dedup_key = combined.lower()
                    if dedup_key in account_hit_keys:
                        continue
                    account_hit_keys.add(dedup_key)
                    hits.append(
                        SocialHit(
                            channel="wechat",
                            provider="sogou",
                            text=combined,
                            published_at=None,
                        )
                    )
                    account_hit_count += 1
            except Exception as error:  # noqa: BLE001
                account_errors.append(f"{term}: {error}")

        if account_hit_count > 0:
            statuses.append(
                ChannelStatus(
                    channel="wechat",
                    provider="sogou",
                    status="ok" if not account_errors else "degraded",
                    message=(
                        f"{account} 命中 {account_hit_count} 条"
                        if not account_errors
                        else f"{account} 命中 {account_hit_count} 条，部分关键词失败"
                    ),
                )
            )
        else:
            msg = f"{account} 无有效命中"
            if account_errors:
                msg += f"，错误: {'; '.join(account_errors[:2])}"
            statuses.append(
                ChannelStatus(
                    channel="wechat",
                    provider="sogou",
                    status="error",
                    message=msg,
                )
            )
            failures.append(f"wechat-{account}: {msg}")

    if not hits and not failures:
        failures.append("wechat: 所有账号均未命中")
    return hits, statuses, failures


def collect_social_bundle() -> SocialBundle:
    hits: list[SocialHit] = []
    statuses: list[ChannelStatus] = []
    failures: list[str] = []

    weibo_hits, weibo_statuses, weibo_failures = fetch_weibo_signals()
    zhihu_hits, zhihu_statuses, zhihu_failures = fetch_zhihu_signals()
    wechat_hits, wechat_statuses, wechat_failures = fetch_wechat_signals(WECHAT_ACCOUNTS)

    hits.extend(weibo_hits)
    hits.extend(zhihu_hits)
    hits.extend(wechat_hits)

    statuses.extend(weibo_statuses)
    statuses.extend(zhihu_statuses)
    statuses.extend(wechat_statuses)

    failures.extend(weibo_failures)
    failures.extend(zhihu_failures)
    failures.extend(wechat_failures)

    status = "ok"
    if failures:
        status = "degraded"

    return SocialBundle(hits=hits, status=status, failures=failures, channels=statuses)


def build_social_index(hits: list[SocialHit]) -> dict[str, list[SocialHit]]:
    index: dict[str, list[SocialHit]] = {}
    for hit in hits:
        index.setdefault(hit.channel, []).append(hit)
    return index


def event_keywords_for_matching(tokens: list[str], title: str) -> list[str]:
    keys: list[str] = []
    for token in tokens:
        if token in EVENT_GENERIC_TERMS:
            continue
        if re.fullmatch(r"[a-z0-9+.-]+", token):
            if len(token) < 4:
                continue
        elif len(token) < 3:
            continue
        keys.append(token)

    if keys:
        return keys[:6]

    normalized = normalize_title(title)
    fallback = [
        t
        for t in re.findall(r"[a-z][a-z0-9]{1,}|[\u4e00-\u9fff]{2,8}", normalized)
        if t and t not in EVENT_GENERIC_TERMS
    ]
    return fallback[:6]


def evaluate_event(
    event_id: str,
    signature_tokens: list[str],
    sample_title: str,
    social_index: dict[str, list[SocialHit]],
) -> EventProfile:
    matched_channels: list[str] = []
    evidence_count = 0
    latest_hit_at: datetime | None = None
    sample_evidence = ""
    keywords = event_keywords_for_matching(signature_tokens, sample_title)
    strong_terms = {
        key for key in keywords if key in EVENT_ALIASES or re.fullmatch(r"[\u4e00-\u9fff]{2,4}", key)
    }

    for channel in ("weibo", "zhihu", "wechat"):
        channel_hits = social_index.get(channel, [])
        channel_evidence: list[SocialHit] = []
        for hit in channel_hits:
            lower_text = hit.text.lower()
            matched_keywords = [keyword for keyword in keywords if keyword in lower_text]
            if not matched_keywords:
                continue
            has_strong = any(keyword in strong_terms for keyword in matched_keywords)
            if has_strong or len(matched_keywords) >= 2:
                channel_evidence.append(hit)

        if channel_evidence:
            matched_channels.append(channel)
            evidence_count += len(channel_evidence)
            if not sample_evidence:
                sample = channel_evidence[0].text
                sample_evidence = f"[{channel}] {sample[:80]}{'...' if len(sample) > 80 else ''}"
            dated_hits = [entry.published_at for entry in channel_evidence if entry.published_at is not None]
            if dated_hits:
                channel_latest = max(dated_hits)
                if latest_hit_at is None or channel_latest > latest_hit_at:
                    latest_hit_at = channel_latest

    categories = ["media", *matched_channels]
    verified = len(categories) >= 2

    channel_base = len(matched_channels) * 7
    evidence_bonus = min(9, evidence_count)
    social_heat_score = min(30, channel_base + evidence_bonus)

    return EventProfile(
        event_id=event_id,
        signature_tokens=signature_tokens,
        matched_channels=matched_channels,
        evidence_count=evidence_count,
        sample_evidence=sample_evidence,
        latest_hit_at=latest_hit_at,
        verified=verified,
        social_heat_score=social_heat_score,
    )


def format_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "未知时间"
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def channels_display(channels: list[str]) -> str:
    return "+".join(channels) if channels else "media"


def save_markdown(items: list[RankedItem], output_dir: Path, run_at: datetime, social: SocialBundle) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    file_name = f"ai_hotspots_{run_at.strftime('%Y-%m-%d')}.md"
    out_file = output_dir / file_name

    lines = [
        "# AI 热点日报",
        "",
        f"- 生成时间: {run_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- 条目数量: {len(items)}",
        f"- 社媒状态: {social.status}",
    ]

    if social.failures:
        lines.append(f"- 社媒失败: {' | '.join(social.failures)}")

    lines.extend(["", "## 社媒通道状态", ""])
    for channel_status in social.channels:
        lines.append(
            f"- {channel_status.channel}/{channel_status.provider}: "
            f"{channel_status.status} - {channel_status.message}"
        )

    lines.append("")

    if not items:
        lines.append("今日未抓取到符合关键词的内容。")
    else:
        for idx, ranked in enumerate(items, start=1):
            item = ranked.item
            lines.append(f"## {idx}. {item.title}")
            lines.append(f"- 来源: {item.source}")
            lines.append(f"- 时间: {format_datetime(item.published_at)}")
            lines.append(f"- 综合分: {ranked.final_score:.2f}")
            lines.append(
                "- 分项得分: "
                f"recency={ranked.recency_score:.2f}, "
                f"media={ranked.media_hotness_score}, "
                f"social={ranked.social_heat_score}, "
                f"verification={ranked.verification_bonus}"
            )
            lines.append(f"- 交叉验证: {channels_display(ranked.verification_channels)}")
            lines.append(f"- 验证证据摘要: {ranked.sample_evidence or '无'}")
            lines.append(f"- 链接: {item.link}")
            if item.summary:
                lines.append(f"- 摘要: {item.summary[:180]}{'...' if len(item.summary) > 180 else ''}")
            lines.append("")

    out_file.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    latest_file = output_dir / "latest_ai_hotspots.md"
    latest_file.write_text(out_file.read_text(encoding="utf-8"), encoding="utf-8")
    return out_file


def ranked_item_to_json_dict(ranked: RankedItem) -> dict[str, object]:
    item = ranked.item
    return {
        "source": item.source,
        "title": item.title,
        "link": item.link,
        "summary": item.summary[:180] + ("..." if len(item.summary) > 180 else ""),
        "published_at_utc": item.published_at.isoformat() if item.published_at else None,
        "published_at_local": format_datetime(item.published_at),
        "hotness_score": ranked.media_hotness_score,
        "event_id": ranked.event_id,
        "final_score": ranked.final_score,
        "score_breakdown": {
            "recency": round(ranked.recency_score, 2),
            "media_hotness": ranked.media_hotness_score,
            "social_heat": ranked.social_heat_score,
            "verification_bonus": ranked.verification_bonus,
        },
        "verification": {
            "verified": len(ranked.verification_channels) >= 2,
            "channels": ranked.verification_channels,
            "evidence_count": ranked.evidence_count,
            "sample_evidence": ranked.sample_evidence,
        },
    }


def save_site_json(items: list[RankedItem], site_dir: Path, run_at: datetime, social: SocialBundle) -> Path:
    data_dir = site_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    local_date = run_at.astimezone().strftime("%Y-%m-%d")
    payload = {
        "generated_at_utc": run_at.isoformat(),
        "generated_at_local": run_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "count": len(items),
        "sources": [{"name": name, "url": url} for name, url in DEFAULT_SOURCES],
        "items": [ranked_item_to_json_dict(item) for item in items],
        "meta": {
            "social_status": social.status,
            "social_failures": social.failures,
            "social_channels": [
                {
                    "channel": c.channel,
                    "provider": c.provider,
                    "status": c.status,
                    "message": c.message,
                }
                for c in social.channels
            ],
        },
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


def dedup_by_link(items: list[FeedItem]) -> list[FeedItem]:
    dedup: dict[str, FeedItem] = {}
    for item in items:
        key = item.link.strip().lower() or item.title.strip().lower()
        existing = dedup.get(key)
        if not existing or sort_key(item) > sort_key(existing):
            dedup[key] = item
    return list(dedup.values())


def select_latest_and_hottest(
    items: list[FeedItem],
    limit: int,
    run_at: datetime,
    social: SocialBundle,
) -> list[RankedItem]:
    if not items:
        return []

    trend_freq = build_trend_frequency(items)
    social_index = build_social_index(social.hits)

    event_groups: dict[str, list[FeedItem]] = {}
    event_tokens: dict[str, list[str]] = {}

    for item in items:
        event_id, tokens = build_event_id(item.title, item.summary)
        event_groups.setdefault(event_id, []).append(item)
        event_tokens[event_id] = tokens

    event_profiles: dict[str, EventProfile] = {}
    for event_id, grouped_items in event_groups.items():
        profile = evaluate_event(
            event_id=event_id,
            signature_tokens=event_tokens.get(event_id, []),
            sample_title=grouped_items[0].title,
            social_index=social_index,
        )
        event_profiles[event_id] = profile

    ranked: list[RankedItem] = []
    for item in items:
        event_id, _ = build_event_id(item.title, item.summary)
        profile = event_profiles[event_id]
        media_hotness = compute_media_hotness_score(item, trend_freq, len(event_groups[event_id]))
        recency_score = compute_recency_score(item.published_at, run_at)
        social_heat_score = profile.social_heat_score
        verification_channels = ["media", *profile.matched_channels]
        verification_bonus = 20 if len(verification_channels) >= 2 else 0
        final_score = compute_final_score(
            recency_score=recency_score,
            media_hotness_score=media_hotness,
            social_heat_score=social_heat_score,
            verification_bonus=verification_bonus,
        )

        ranked.append(
            RankedItem(
                item=item,
                event_id=event_id,
                media_hotness_score=media_hotness,
                recency_score=recency_score,
                social_heat_score=social_heat_score,
                verification_bonus=verification_bonus,
                final_score=final_score,
                verification_channels=verification_channels,
                evidence_count=profile.evidence_count,
                sample_evidence=profile.sample_evidence,
            )
        )

    ranked.sort(
        key=lambda row: (
            row.final_score,
            sort_key(row.item),
        ),
        reverse=True,
    )

    selected: list[RankedItem] = []
    seen_event_ids: set[str] = set()

    for row in ranked:
        if len(selected) >= limit:
            break
        if row.event_id in seen_event_ids:
            continue
        selected.append(row)
        seen_event_ids.add(row.event_id)

    if len(selected) < limit:
        selected_links = {row.item.link for row in selected}
        for row in ranked:
            if len(selected) >= limit:
                break
            if row.item.link in selected_links:
                continue
            selected.append(row)
            selected_links.add(row.item.link)

    return selected


def main() -> int:
    args = build_args()
    run_at = datetime.now(timezone.utc)
    earliest = run_at - timedelta(days=max(args.days, 1))
    all_items: list[FeedItem] = []

    for source_name, source_url in DEFAULT_SOURCES:
        try:
            xml_text = fetch_text(source_url)
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

    dedup_items = dedup_by_link(all_items)

    social_bundle = collect_social_bundle()
    selected = select_latest_and_hottest(
        dedup_items,
        max(args.limit, 1),
        run_at,
        social_bundle,
    )

    output_path = save_markdown(selected, Path(args.output_dir), run_at, social_bundle)
    site_json = save_site_json(selected, Path(args.site_dir), run_at, social_bundle)

    print(f"[OK] 已生成 {len(selected)} 条: {output_path}")
    print(f"[OK] 网站数据已更新: {site_json}")
    if social_bundle.failures:
        print(f"[WARN] 社媒降级: {' | '.join(social_bundle.failures)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
