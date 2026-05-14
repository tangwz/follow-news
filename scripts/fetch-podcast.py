#!/usr/bin/env python3
"""
Fetch podcast and YouTube episode metadata from unified sources configuration.
"""

import argparse
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

try:
    import feedparser

    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False

TIMEOUT = 30
MAX_EPISODES_PER_SOURCE = 20
PODCAST_CACHE_PATH = "/tmp/follow-news-podcast-cache.json"
TRANSCRIPT_SUCCESS_TTL_SECONDS = 30 * 86400
TRANSCRIPT_FAILURE_TTL_SECONDS = 6 * 3600


def setup_logging(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


def infer_platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        return "youtube"
    return "rss"


def parse_podcast_date(value: str) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value or "").strip()


def extract_cdata(value: str) -> str:
    match = re.search(r"<!\[CDATA\[(.*?)\]\]>", value or "", re.DOTALL)
    return match.group(1) if match else (value or "")


def get_tag(xml: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", xml, re.DOTALL | re.IGNORECASE)
    return extract_cdata(match.group(1)).strip() if match else ""


def resolve_link(link: str, base_url: str) -> str:
    if not link:
        return ""
    if link.startswith(("http://", "https://")):
        return link
    resolved = urljoin(base_url, link)
    if resolved.startswith(("http://", "https://")):
        return resolved
    return ""


def transcript_config(source: Dict[str, Any]) -> Dict[str, Any]:
    value = source.get("transcript")
    if isinstance(value, dict):
        return value
    return {"enabled": False}


def build_episode(
    source: Dict[str, Any],
    title: str,
    link: str,
    published: datetime,
    guid: str,
    platform: str,
) -> Dict[str, Any]:
    config = transcript_config(source)
    episode = {
        "title": title[:300],
        "link": link,
        "date": published.astimezone(timezone.utc).isoformat(),
        "guid": guid or link,
        "topics": list(source.get("topics", [])),
        "show_name": source.get("name", ""),
        "platform": platform,
        "transcript_status": "disabled",
    }
    if config.get("enabled"):
        episode["transcript_status"] = "missing"
    return episode


def parse_rss_episodes(
    content: str,
    source: Dict[str, Any],
    cutoff: datetime,
) -> List[Dict[str, Any]]:
    feed_url = source.get("url", "")
    episodes: List[Dict[str, Any]] = []
    if HAS_FEEDPARSER:
        feed = feedparser.parse(content)
        for entry in feed.entries[:MAX_EPISODES_PER_SOURCE]:
            title = str(entry.get("title", "")).strip()
            link = resolve_link(str(entry.get("link", "")).strip(), feed_url)
            guid = str(entry.get("id") or entry.get("guid") or link)
            date_value = str(entry.get("published") or entry.get("updated") or "")
            published = parse_podcast_date(date_value)
            if title and link and published and published >= cutoff:
                episodes.append(build_episode(source, title, link, published, guid, "rss"))
        if episodes:
            return episodes

    for item in re.finditer(r"<item[^>]*>(.*?)</item>", content, re.DOTALL | re.IGNORECASE):
        block = item.group(1)
        title = strip_tags(get_tag(block, "title"))
        link = resolve_link(get_tag(block, "link"), feed_url)
        guid = get_tag(block, "guid") or link
        published = parse_podcast_date(get_tag(block, "pubDate") or get_tag(block, "dc:date"))
        if title and link and published and published >= cutoff:
            episodes.append(build_episode(source, title, link, published, guid, "rss"))
    return episodes[:MAX_EPISODES_PER_SOURCE]


def load_podcast_sources(defaults_dir: Path, config_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    try:
        from config_loader import load_merged_sources
    except ImportError:
        sys.path.append(str(Path(__file__).parent))
        from config_loader import load_merged_sources

    all_sources = load_merged_sources(defaults_dir, config_dir)
    sources = [
        source
        for source in all_sources
        if source.get("type") == "podcast" and source.get("enabled", True)
    ]
    logging.info("Loaded %d enabled podcast sources", len(sources))
    return sources
