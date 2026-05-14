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
YOUTUBE_METADATA_WORKERS = 4
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


def is_http_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


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
        if link and not is_http_url(link):
            link = ""
        published = youtube_entry_published_at(entry)
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


def youtube_entry_published_at(entry: Dict[str, Any]) -> Optional[datetime]:
    return timestamp_to_datetime(entry.get("timestamp")) or parse_youtube_date(
        entry.get("upload_date") or entry.get("release_date")
    )


def youtube_entry_link(entry: Dict[str, Any]) -> str:
    link = str(entry.get("webpage_url") or entry.get("url") or "").strip()
    video_id = str(entry.get("id") or youtube_video_id(link)).strip()
    if link and is_http_url(link):
        return link
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return ""


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
        normalized = [str(language).strip() for language in languages if str(language).strip()]
        if normalized:
            return normalized
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
        except OSError as exc:
            return {"status": "error", "error": str(exc)[:200]}

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
    last_text = ""
    in_note = False
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            in_note = False
            continue
        if in_note:
            continue
        if line.startswith("NOTE"):
            in_note = True
            continue
        if line == "WEBVTT" or line.startswith(("Kind:", "Language:")):
            continue
        if "-->" in line:
            current_time = line.split("-->", 1)[0].strip()
            continue
        if re.match(r"^\d+$", line):
            continue
        text = re.sub(r"<[^>]+>", "", line).strip()
        if text:
            if text == last_text:
                continue
            if current_time:
                lines.append(f"{current_time} {text}")
            else:
                lines.append(text)
            last_text = text
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


def load_podcast_cache(no_cache: bool = False) -> Dict[str, Any]:
    if no_cache:
        return {"transcripts": {}}

    try:
        with open(PODCAST_CACHE_PATH, "r", encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"transcripts": {}}

    if not isinstance(cache, dict):
        return {"transcripts": {}}
    if not isinstance(cache.get("transcripts"), dict):
        cache["transcripts"] = {}
    return cache


def save_podcast_cache(cache: Dict[str, Any]) -> None:
    cache_path = Path(PODCAST_CACHE_PATH)
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(cache_path.parent),
            prefix="follow-news-podcast-cache-",
            suffix=".json",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(cache, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, cache_path)
    except OSError as exc:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        logging.warning("Failed to write podcast cache: %s", exc)


def fetch_rss_source(
    source: Dict[str, Any],
    cutoff: datetime,
    cache: Dict[str, Any],
    no_cache: bool,
) -> List[Dict[str, Any]]:
    request = Request(
        source["url"],
        headers={"User-Agent": "FollowNews/2.0"},
    )
    with urlopen(request, timeout=TIMEOUT) as response:
        content = response.read().decode("utf-8", errors="replace")

    episodes = parse_rss_episodes(content, source, cutoff)
    return [
        enrich_episode_transcript(episode, source, cache, no_cache=no_cache)
        for episode in episodes
    ]


def run_ytdlp_metadata(
    ytdlp_bin: str,
    source: Dict[str, Any],
    timeout: int = 90,
) -> Dict[str, Any]:
    import subprocess

    cmd = [
        ytdlp_bin,
        "--dump-single-json",
        "--flat-playlist",
        "--playlist-end",
        str(MAX_EPISODES_PER_SOURCE),
        source["url"],
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("yt-dlp metadata command timed out") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "yt-dlp metadata command failed").strip()
        raise RuntimeError(message[:300])

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp metadata output was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("yt-dlp metadata output was not an object")
    return payload


def run_ytdlp_video_metadata(
    ytdlp_bin: str,
    url: str,
    timeout: int = 90,
) -> Dict[str, Any]:
    import subprocess

    cmd = [
        ytdlp_bin,
        "--dump-single-json",
        "--skip-download",
        "--no-playlist",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("yt-dlp video metadata command timed out") from exc
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "yt-dlp video metadata command failed").strip()
        raise RuntimeError(message[:300])

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp video metadata output was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("yt-dlp video metadata output was not an object")
    return payload


def hydrate_youtube_metadata(
    payload: Dict[str, Any],
    ytdlp_bin: str,
) -> Dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    raw_entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(raw_entries, list):
        return payload

    hydrated_entries: List[Dict[str, Any]] = []
    hydrate_jobs: Dict[int, str] = {}
    for entry in raw_entries[:MAX_EPISODES_PER_SOURCE]:
        if not isinstance(entry, dict):
            continue
        hydrated_entries.append(entry)
        if youtube_entry_published_at(entry):
            continue

        link = youtube_entry_link(entry)
        if not link:
            continue
        hydrate_jobs[len(hydrated_entries) - 1] = link

    if not hydrate_jobs:
        return {**payload, "entries": hydrated_entries}

    max_workers = min(YOUTUBE_METADATA_WORKERS, len(hydrate_jobs))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(run_ytdlp_video_metadata, ytdlp_bin, link): (index, link)
            for index, link in hydrate_jobs.items()
        }
        for future in as_completed(futures):
            index, link = futures[future]
            try:
                video_metadata = future.result()
            except RuntimeError as exc:
                logging.warning("Failed to hydrate YouTube podcast metadata for %s: %s", link, exc)
                continue
            hydrated_entries[index] = {**hydrated_entries[index], **video_metadata}

    return {**payload, "entries": hydrated_entries}


def fetch_youtube_source(
    source: Dict[str, Any],
    cutoff: datetime,
    cache: Dict[str, Any],
    no_cache: bool,
) -> List[Dict[str, Any]]:
    ytdlp_bin = resolve_ytdlp_bin()
    if not ytdlp_bin:
        raise RuntimeError("yt-dlp is not available")

    payload = run_ytdlp_metadata(ytdlp_bin, source)
    payload = hydrate_youtube_metadata(payload, ytdlp_bin)
    episodes = normalize_youtube_metadata(payload, source, cutoff)
    return [
        enrich_episode_transcript(episode, source, cache, no_cache=no_cache)
        for episode in episodes
    ]


def fetch_source(
    source: Dict[str, Any],
    cutoff: datetime,
    cache: Dict[str, Any],
    no_cache: bool = False,
) -> Dict[str, Any]:
    platform = source.get("platform")
    if not platform or platform == "auto":
        platform = infer_platform(source.get("url", ""))

    result = {
        "source_id": source.get("id", ""),
        "source_type": "podcast",
        "name": source.get("name", ""),
        "url": source.get("url", ""),
        "priority": bool(source.get("priority", False)),
        "topics": list(source.get("topics", [])),
        "platform": platform,
        "status": "ok",
        "attempts": 1,
        "count": 0,
        "articles": [],
    }

    try:
        if platform == "youtube":
            articles = fetch_youtube_source(source, cutoff, cache, no_cache)
        elif platform == "rss":
            articles = fetch_rss_source(source, cutoff, cache, no_cache)
        else:
            raise RuntimeError(f"Unsupported podcast platform: {platform}")
    except Exception as exc:
        result["status"] = "error"
        result["error"] = str(exc)[:300]
        return result

    result["articles"] = articles
    result["count"] = len(articles)
    return result


def run_fetch(
    sources: List[Dict[str, Any]],
    hours: int,
    output: Path,
    cache: Dict[str, Any],
    no_cache: bool = False,
) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    source_results = [
        fetch_source(source, cutoff, cache, no_cache=no_cache)
        for source in sources
    ]
    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source_type": "podcast",
        "total_sources": len(source_results),
        "total_articles": sum(int(result.get("count", 0)) for result in source_results),
        "sources": source_results,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def output_cache_is_fresh(output: Path, max_age_seconds: int = 3600) -> bool:
    if not output.exists():
        return False
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
        age = time.time() - output.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get("source_type") != "podcast":
        return False
    if not isinstance(payload.get("sources"), list):
        return False
    return age < max_age_seconds


def create_default_output_path() -> Path:
    fd, path = tempfile.mkstemp(prefix="follow-news-podcast-", suffix=".json")
    os.close(fd)
    return Path(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch podcast episode metadata")
    parser.add_argument("--defaults", type=Path, default=Path("config/defaults"))
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--hours", type=int, default=336)
    parser.add_argument("--output", "-o", type=Path, default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    setup_logging(args.verbose)

    output = args.output or create_default_output_path()
    if not args.force and output_cache_is_fresh(output):
        logging.info("Using fresh podcast output: %s", output)
        return 0

    sources = load_podcast_sources(args.defaults, args.config)
    cache = load_podcast_cache(no_cache=args.no_cache)
    result = run_fetch(
        sources,
        args.hours,
        output,
        cache,
        no_cache=args.no_cache,
    )
    if not args.no_cache:
        save_podcast_cache(cache)
    logging.info("Wrote podcast output: %s", output)
    return result


if __name__ == "__main__":
    sys.exit(main())
