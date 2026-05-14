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


def youtube_video_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host == "youtu.be":
        return parsed.path.strip("/")
    if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parsed.query.split("&") if parsed.query else []
        for part in query:
            if part.startswith("v="):
                return part.split("=", 1)[1]
    return ""


def timestamp_to_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def parse_youtube_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if re.match(r"^\d{8}$", text):
        try:
            return datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return parse_podcast_date(text)


def normalize_youtube_metadata(
    payload: Dict[str, Any],
    source: Dict[str, Any],
    cutoff: datetime,
) -> List[Dict[str, Any]]:
    raw_entries = payload.get("entries") if isinstance(payload, dict) else None
    if raw_entries is None:
        raw_entries = [payload]

    episodes: List[Dict[str, Any]] = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue

        title = str(entry.get("title") or "").strip()
        link = str(entry.get("webpage_url") or entry.get("url") or "").strip()
        video_id = str(entry.get("id") or youtube_video_id(link)).strip()
        published = timestamp_to_datetime(entry.get("timestamp")) or parse_youtube_date(
            entry.get("upload_date") or entry.get("release_date")
        )
        if not link and video_id:
            link = f"https://www.youtube.com/watch?v={video_id}"
        if not title or not link or not video_id or not published or published < cutoff:
            continue

        episode = build_episode(
            source,
            title,
            link,
            published,
            f"youtube:{video_id}",
            "youtube",
        )
        duration = entry.get("duration")
        if isinstance(duration, (int, float)) and duration > 0:
            episode["duration_seconds"] = int(duration)
        episodes.append(episode)

    return episodes[:MAX_EPISODES_PER_SOURCE]


def resolve_ytdlp_bin() -> Optional[str]:
    configured = os.environ.get("YTDLP_BIN") or os.environ.get("YT_DLP_BIN")
    if configured:
        return configured

    from shutil import which

    return which("yt-dlp")


def transcript_cache_key(episode: Dict[str, Any]) -> str:
    return str(episode.get("guid") or episode.get("link") or episode.get("title") or "")


def transcript_languages(source: Dict[str, Any]) -> List[str]:
    config = transcript_config(source)
    languages = config.get("languages")
    if isinstance(languages, list) and languages:
        return [str(language) for language in languages if str(language).strip()]
    return ["en", "zh", "zh-Hans"]


def run_ytdlp_transcript(
    ytdlp_bin: str,
    episode: Dict[str, Any],
    languages: List[str],
    timeout: int = 60,
) -> Dict[str, str]:
    import subprocess

    with tempfile.TemporaryDirectory(prefix="follow-news-ytdlp-") as tmpdir:
        cmd = [
            ytdlp_bin,
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            ",".join(languages),
            "--sub-format",
            "vtt",
            "--convert-subs",
            "vtt",
            "--output",
            str(Path(tmpdir) / "%(id)s.%(ext)s"),
            episode["link"],
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "error": "yt-dlp transcript command timed out"}

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "yt-dlp transcript command failed").strip()
            return {"status": "error", "error": message[:200]}

        vtt_files = sorted(Path(tmpdir).glob("*.vtt"))
        if not vtt_files:
            return {"status": "missing", "error": "No subtitle track found"}

        try:
            transcript = parse_vtt_transcript(
                vtt_files[0].read_text(encoding="utf-8", errors="replace")
            )
        except OSError as exc:
            return {"status": "parse_error", "error": str(exc)[:200]}
        if not transcript.strip():
            return {"status": "parse_error", "error": "Subtitle file did not contain transcript text"}
        return {"status": "ok", "transcript": transcript}


def parse_vtt_transcript(content: str) -> str:
    lines: List[str] = []
    current_time = ""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT" or line.startswith(("NOTE", "Kind:", "Language:")):
            continue
        if "-->" in line:
            current_time = line.split("-->", 1)[0].strip()
            continue
        if re.match(r"^\d+$", line):
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if text:
            if current_time:
                lines.append(f"{current_time} {text}")
            else:
                lines.append(text)
    return "\n".join(lines)


def cache_entry_valid(entry: Dict[str, Any], now: float) -> bool:
    status = entry.get("status")
    ttl = TRANSCRIPT_SUCCESS_TTL_SECONDS if status == "ok" else TRANSCRIPT_FAILURE_TTL_SECONDS
    try:
        ts = float(entry.get("ts", 0))
    except (TypeError, ValueError):
        return False
    return now - ts < ttl


def enrich_episode_transcript(
    episode: Dict[str, Any],
    source: Dict[str, Any],
    cache: Dict[str, Any],
    no_cache: bool = False,
) -> Dict[str, Any]:
    config = transcript_config(source)
    if not config.get("enabled"):
        episode["transcript_status"] = "disabled"
        return episode

    key = transcript_cache_key(episode)
    now = time.time()
    if key and not no_cache:
        cached = cache.get("transcripts", {}).get(key)
        if isinstance(cached, dict) and cache_entry_valid(cached, now):
            episode["transcript_status"] = cached.get("status", "error")
            if cached.get("transcript"):
                episode["transcript"] = cached["transcript"]
            if cached.get("error"):
                episode["transcript_error"] = cached["error"]
            return episode

    backend = config.get("backend", "auto")
    if backend not in {"auto", "yt-dlp"}:
        episode["transcript_status"] = "error"
        episode["transcript_error"] = f"Unsupported transcript backend: {backend}"
        return episode

    ytdlp_bin = resolve_ytdlp_bin()
    if not ytdlp_bin:
        result = {"status": "backend_unavailable", "error": "yt-dlp is not available"}
    else:
        result = run_ytdlp_transcript(ytdlp_bin, episode, transcript_languages(source))

    episode["transcript_status"] = result.get("status", "error")
    episode.pop("transcript", None)
    episode.pop("transcript_error", None)
    if result.get("transcript"):
        episode["transcript"] = result["transcript"]
    if result.get("error"):
        episode["transcript_error"] = result["error"]

    if key:
        cache.setdefault("transcripts", {})[key] = {
            "status": episode["transcript_status"],
            "transcript": episode.get("transcript", ""),
            "error": episode.get("transcript_error", ""),
            "ts": now,
        }
    return episode


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
