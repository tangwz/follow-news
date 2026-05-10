#!/usr/bin/env python3
"""
Fetch Twitter/X posts from KOL accounts using X API.

Reads sources.json, filters Twitter sources, fetches recent posts using
either the official X API v2 or twitterapi.io, and outputs structured JSON.

Usage:
    python3 fetch-twitter.py [--config CONFIG_DIR] [--hours 48] [--output FILE] [--verbose]
    python3 fetch-twitter.py --backend twitterapiio  # force twitterapi.io backend

Environment:
    TWITTER_API_BACKEND - Backend selection: "auto" (default), "getxapi", "twitterapiio", or "official"
                        Auto priority: getxapi ($0.001/call) > twitterapi.io (~$5/mo) > official X API
    OPENCLI_MAX_WORKERS  - OpenCLI fetch concurrency for twitter tweets (1-10, default: 10)
    GETX_API_KEY        - GetXAPI API key (preferred backend, $0.001 per call)
    TWITTERAPI_IO_KEY   - twitterapi.io API key (alternative backend, ~$5/month)
    X_BEARER_TOKEN      - Twitter/X official API v2 bearer token (fallback)
"""

import json
import sys
import os
import argparse
import logging
import time
import tempfile
import re
import threading
import subprocess
import shutil
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import HTTPError
from urllib.parse import urlencode, quote
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

TIMEOUT = 30
MAX_WORKERS = 5  # Lower for API rate limits
RETRY_COUNT = 2
RETRY_DELAY = 2.0
MAX_TWEETS_PER_USER = 20
OPENCLI_TIMEOUT = 90
OPENCLI_TAB_COMMAND_TIMEOUT = 15
OPENCLI_DEFAULT_MAX_WORKERS = 10
OPENCLI_MAX_WORKERS_MAX = 10
OPENCLI_CLOSE_TABS_AFTER_RUN_DEFAULT = True
OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN_DEFAULT = True
OPENCLI_GLOBAL_ERROR_CODES = {
    "opencli_missing",
    "opencli_capability_missing",
    "opencli_browser_unavailable",
    "opencli_auth_required",
    "opencli_timeout",
    "opencli_parse_error",
}
ID_CACHE_PATH = "/tmp/follow-news-twitter-id-cache.json"
ID_CACHE_TTL_DAYS = 7

# Twitter API v2 endpoints
OFFICIAL_API_BASE = "https://api.x.com/2"
USER_LOOKUP_ENDPOINT = f"{OFFICIAL_API_BASE}/users/by"

# twitterapi.io endpoints
TWITTERAPIIO_BASE = "https://api.twitterapi.io"
GETXAPI_BASE = "https://api.getxapi.com"

CHROME_WINDOW_SNAPSHOT_SCRIPT = r'''
tell application "Google Chrome"
  if it is not running then return ""
  set rows to {}
  repeat with chromeWindow in windows
    set tabUrls to {}
    repeat with chromeTab in tabs of chromeWindow
      set end of tabUrls to URL of chromeTab
    end repeat
    set oldDelimiters to AppleScript's text item delimiters
    set AppleScript's text item delimiters to " ||| "
    set urlText to tabUrls as text
    set AppleScript's text item delimiters to oldDelimiters
    set end of rows to ((id of chromeWindow as text) & (character id 9) & urlText)
  end repeat
  set oldDelimiters to AppleScript's text item delimiters
  set AppleScript's text item delimiters to linefeed
  set outputText to rows as text
  set AppleScript's text item delimiters to oldDelimiters
  return outputText
end tell
'''

CHROME_WINDOW_CLOSE_SCRIPT = r'''
on run argv
  tell application "Google Chrome"
    if it is not running then return "Chrome not running"
    repeat with rawWindowId in argv
      try
        repeat with chromeWindow in windows
          if (id of chromeWindow as text) is (rawWindowId as text) then
            close chromeWindow
            exit repeat
          end if
        end repeat
      end try
    end repeat
  end tell
end run
'''


def setup_logging(verbose: bool) -> logging.Logger:
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(__name__)


def clean_tweet_text(text: str) -> str:
    """Clean tweet text for better display."""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Truncate if too long
    if len(text) > 280:
        text = text[:277] + "..."
    return text


class OpenCliBackendError(RuntimeError):
    """Error raised when OpenCLI cannot be used as a backend."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _parse_opencli_date(date_str: str) -> Optional[datetime]:
    """Parse OpenCLI Twitter date formats into an aware datetime."""
    if not date_str:
        return None

    value = str(date_str).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        pass

    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(str(date_str), fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue

    logging.debug(f"Failed to parse OpenCLI date: {date_str}")
    return None


def _as_int(value: Any) -> int:
    """Best-effort conversion for OpenCLI metric fields."""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def extract_opencli_tweet_records(payload: Any) -> List[Dict[str, Any]]:
    """Extract tweet records from common OpenCLI JSON output envelopes."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("tweets", "items", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return extract_opencli_tweet_records(data)

    return []


def extract_opencli_browser_tabs(payload: Any) -> Dict[str, str]:
    """Extract browser tab target IDs and URLs from OpenCLI tab-list JSON."""
    if isinstance(payload, list):
        tabs = {}
        for item in payload:
            tabs.update(extract_opencli_browser_tabs(item))
        return tabs

    if isinstance(payload, dict):
        target_id = payload.get("targetId") or payload.get("target_id") or payload.get("page") or payload.get("id")
        if target_id:
            url = payload.get("url") or payload.get("href") or payload.get("location") or ""
            return {str(target_id): str(url)}

        tabs = {}
        for key in ("tabs", "items", "data", "targets"):
            if key in payload:
                tabs.update(extract_opencli_browser_tabs(payload[key]))
        return tabs

    return {}


def is_twitter_browser_tab(url: str) -> bool:
    """Return true for X/Twitter browser tabs created by OpenCLI."""
    value = (url or "").lower()
    return value.startswith(("https://x.com/", "https://twitter.com/", "https://mobile.twitter.com/"))


def parse_chrome_window_snapshot(raw_value: str) -> Dict[str, List[str]]:
    """Parse Chrome window snapshots emitted by the AppleScript helper."""
    windows: Dict[str, List[str]] = {}
    for line in (raw_value or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if "\t" in line:
            window_id, urls = line.split("\t", 1)
        elif "tab" in line:
            window_id, urls = line.split("tab", 1)
        else:
            continue
        window_id = window_id.strip()
        if not window_id:
            continue
        windows[window_id] = [url.strip() for url in urls.split(" ||| ") if url.strip()]
    return windows


def is_opencli_chrome_window(urls: List[str]) -> bool:
    """Return true for Chrome windows that look like OpenCLI automation leftovers."""
    if not urls:
        return False
    automation_urls = {"about:blank", "chrome://newtab/"}
    for url in urls:
        value = (url or "").lower()
        if value in automation_urls:
            continue
        if is_twitter_browser_tab(value):
            continue
        return False
    return True


def normalize_opencli_tweet(
    record: Dict[str, Any],
    source: Dict[str, Any],
    cutoff: datetime,
) -> Optional[Dict[str, Any]]:
    """Normalize one OpenCLI tweet into the existing Twitter article shape."""
    tweet_id = str(record.get("id") or record.get("tweet_id") or "").strip()
    text = str(record.get("text") or record.get("full_text") or "").strip()
    created_at = _parse_opencli_date(record.get("created_at") or record.get("date") or "")

    if not tweet_id or not text or not created_at:
        return None
    if created_at < cutoff:
        return None
    if record.get("is_retweet") or text.startswith("RT @"):
        return None

    handle = source["handle"].lstrip("@")
    link = record.get("url") or record.get("link") or f"https://x.com/{handle}/status/{tweet_id}"

    return {
        "title": clean_tweet_text(text),
        "link": link,
        "date": created_at.isoformat(),
        "topics": source["topics"][:],
        "metrics": {
            "like_count": _as_int(record.get("likes") or record.get("like_count")),
            "retweet_count": _as_int(record.get("retweets") or record.get("retweet_count")),
            "reply_count": _as_int(record.get("replies") or record.get("reply_count")),
            "quote_count": _as_int(record.get("quotes") or record.get("quote_count")),
            "impression_count": _as_int(record.get("views") or record.get("impression_count")),
        },
        "tweet_id": tweet_id,
    }


def resolve_opencli_bin() -> str:
    """Resolve the OpenCLI executable from OPENCLI_BIN or PATH."""
    configured = os.getenv("OPENCLI_BIN")
    if configured:
        return configured

    found = shutil.which("opencli")
    if found:
        return found

    raise OpenCliBackendError(
        "opencli_missing",
        "OpenCLI executable not found. Set OPENCLI_BIN or install opencli on PATH.",
    )


def opencli_has_twitter_tweets(payload: Any) -> bool:
    """Return true when `opencli list -f json` exposes twitter tweets."""
    if isinstance(payload, list):
        for item in payload:
            if opencli_has_twitter_tweets(item):
                return True
        return False

    if isinstance(payload, str):
        value = " ".join(payload.lower().strip().split())
        return value in {"twitter tweets", "opencli twitter tweets"}

    if isinstance(payload, dict):
        site = str(payload.get("site") or payload.get("group") or "").lower()
        name = str(payload.get("name") or payload.get("command") or "").lower()
        command = " ".join(name.strip().split())
        if site == "twitter" and command == "tweets":
            return True
        if command in {"twitter tweets", "opencli twitter tweets"}:
            return True

        if "twitter" in payload:
            twitter_value = payload["twitter"]
            if isinstance(twitter_value, list):
                return "tweets" in [str(item).lower() for item in twitter_value]
            if isinstance(twitter_value, dict):
                return opencli_has_twitter_tweets(twitter_value)

        for key in ("commands", "items", "data", "sites"):
            if key in payload and opencli_has_twitter_tweets(payload[key]):
                return True

    return False


def get_backend_order(backend_name: str) -> List[str]:
    """Return backend candidates in the order they should be attempted."""
    if backend_name == "auto":
        return ["opencli", "getxapi", "twitterapiio", "official"]
    return [backend_name]


def get_opencli_max_workers(raw_value: Optional[str] = None) -> int:
    """Return OpenCLI worker count, defaulting to parallel browser access."""
    if raw_value is None:
        raw_value = os.getenv("OPENCLI_MAX_WORKERS", "").strip()
    else:
        raw_value = str(raw_value).strip()

    if not raw_value:
        return OPENCLI_DEFAULT_MAX_WORKERS

    try:
        value = int(raw_value)
    except ValueError:
        logging.warning("Invalid OPENCLI_MAX_WORKERS=%r; using %s", raw_value, OPENCLI_DEFAULT_MAX_WORKERS)
        return OPENCLI_DEFAULT_MAX_WORKERS

    if value < 1:
        logging.warning("Invalid OPENCLI_MAX_WORKERS=%r; using %s", raw_value, OPENCLI_DEFAULT_MAX_WORKERS)
        return OPENCLI_DEFAULT_MAX_WORKERS
    if value > OPENCLI_MAX_WORKERS_MAX:
        logging.warning(
            "OPENCLI_MAX_WORKERS=%r exceeds max (%s); clamping to %s",
            raw_value,
            OPENCLI_MAX_WORKERS_MAX,
            OPENCLI_MAX_WORKERS_MAX,
        )
        return OPENCLI_MAX_WORKERS_MAX

    return value


def get_opencli_close_tabs_after_run() -> bool:
    """Return whether OpenCLI-created Twitter tabs should be closed after fetch."""
    raw_value = os.getenv("OPENCLI_CLOSE_TABS_AFTER_RUN", "").strip().lower()
    if not raw_value:
        return OPENCLI_CLOSE_TABS_AFTER_RUN_DEFAULT
    return raw_value not in {"0", "false", "no", "off"}


def get_opencli_close_chrome_windows_after_run() -> bool:
    """Return whether OpenCLI-created Chrome windows should be closed after fetch."""
    raw_value = os.getenv("OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN", "").strip().lower()
    if not raw_value:
        return OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN_DEFAULT
    return raw_value not in {"0", "false", "no", "off"}


def snapshot_chrome_windows() -> Optional[Dict[str, List[str]]]:
    """Return a best-effort snapshot of Chrome windows on macOS."""
    if not get_opencli_close_chrome_windows_after_run():
        return None
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return None

    result = subprocess.run(
        ["osascript", "-e", CHROME_WINDOW_SNAPSHOT_SCRIPT],
        capture_output=True,
        text=True,
        timeout=OPENCLI_TAB_COMMAND_TIMEOUT,
    )
    if result.returncode != 0:
        logging.warning("Chrome window snapshot failed: %s", (result.stderr or result.stdout).strip()[:200])
        return None
    return parse_chrome_window_snapshot(result.stdout)


def close_chrome_windows(window_ids: List[str]) -> None:
    """Close Chrome windows by id on macOS."""
    if not window_ids or sys.platform != "darwin" or not shutil.which("osascript"):
        return

    result = subprocess.run(
        ["osascript", "-e", CHROME_WINDOW_CLOSE_SCRIPT] + window_ids,
        capture_output=True,
        text=True,
        timeout=OPENCLI_TAB_COMMAND_TIMEOUT,
    )
    if result.returncode != 0:
        logging.warning("Chrome window close failed: %s", (result.stderr or result.stdout).strip()[:200])


def cleanup_new_opencli_chrome_windows(before_windows: Optional[Dict[str, List[str]]]) -> None:
    """Close Chrome automation windows created during the OpenCLI fetch."""
    if before_windows is None:
        return

    closed_any = False
    for _ in range(6):
        time.sleep(0.5)
        after_windows = snapshot_chrome_windows()
        if after_windows is None:
            return

        new_window_ids = [
            window_id
            for window_id, urls in after_windows.items()
            if window_id not in before_windows and is_opencli_chrome_window(urls)
        ]
        if new_window_ids:
            close_chrome_windows(new_window_ids)
            closed_any = True
            continue
        if closed_any:
            return


def build_twitter_skipped_reason(backend_name: str, diagnostics: List[Dict[str, str]]) -> str:
    """Build a human-readable reason for an empty Twitter output."""
    opencli_diag = next((item for item in diagnostics if item.get("backend") == "opencli"), None)
    unavailable = {item.get("backend") for item in diagnostics if item.get("code") == "backend_unavailable"}

    parts = []
    if opencli_diag:
        message = opencli_diag.get("message", "").strip()
        if len(message) > 160:
            message = message[:157] + "..."
        detail = opencli_diag.get("code", "unknown")
        if message:
            detail = f"{detail}: {message}"
        parts.append(f"OpenCLI failed first ({detail})")

    missing = []
    if "getxapi" in unavailable:
        missing.append("GETX_API_KEY")
    if "twitterapiio" in unavailable:
        missing.append("twitterapi.io/TWITTERAPI_IO_KEY")
    if "official" in unavailable:
        missing.append("official X API/X_BEARER_TOKEN")

    if missing:
        parts.append("API fallbacks unavailable: " + ", ".join(missing))

    if parts:
        return "; ".join(parts)

    return f"No usable backend for '{backend_name}'"


def _classify_opencli_failure(returncode: int, stderr: str) -> str:
    """Map OpenCLI process failures into stable backend error codes."""
    text = (stderr or "").lower()
    if returncode == 69:
        return "opencli_browser_unavailable"
    if returncode == 75:
        return "opencli_timeout"
    if returncode == 77:
        return "opencli_auth_required"
    if "not logged" in text or "auth" in text or "permission" in text:
        return "opencli_auth_required"
    if "no tab with id" in text or ("securityerror" in text and "pushstate" in text):
        return "opencli_browser_unavailable"
    if "browser" in text and ("unavailable" in text or "connect" in text):
        return "opencli_browser_unavailable"
    return "opencli_source_error"


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple token-bucket rate limiter."""
    def __init__(self, qps: float):
        self._lock = threading.Lock()
        self._min_interval = 1.0 / qps
        self._last = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            wait_time = self._min_interval - (now - self._last)
            if wait_time > 0:
                time.sleep(wait_time)
            self._last = time.monotonic()


# ---------------------------------------------------------------------------
# Backend abstraction
# ---------------------------------------------------------------------------

class TwitterBackend(ABC):
    """Base class for Twitter API backends."""

    @staticmethod
    def _make_result(source, articles, attempt):
        return {
            "source_id": source["id"],
            "source_type": "twitter",
            "name": source["name"],
            "handle": source["handle"].lstrip('@'),
            "priority": source["priority"],
            "topics": source["topics"],
            "status": "ok",
            "attempts": attempt + 1,
            "count": len(articles),
            "articles": articles,
        }

    @staticmethod
    def _make_error(source, error_msg, attempt):
        return {
            "source_id": source["id"],
            "source_type": "twitter",
            "name": source["name"],
            "handle": source["handle"].lstrip('@'),
            "priority": source["priority"],
            "topics": source["topics"],
            "status": "error",
            "attempts": attempt + 1,
            "error": error_msg,
            "count": 0,
            "articles": [],
        }

    @abstractmethod
    def fetch_all(self, sources: List[Dict[str, Any]], cutoff: datetime) -> List[Dict[str, Any]]:
        """Fetch tweets for all sources. Returns list of source result dicts."""


class OpenCliBackend(TwitterBackend):
    """OpenCLI backend using the browser-backed twitter tweets adapter."""

    def __init__(self, command: Optional[str] = None, max_workers: Optional[int] = None):
        self.command = command or resolve_opencli_bin()
        self._max_workers = max_workers
        self._before_chrome_windows = snapshot_chrome_windows()
        try:
            self._verify_capability()
            self._run_doctor()
        except Exception:
            cleanup_new_opencli_chrome_windows(self._before_chrome_windows)
            raise

    def _run_command(self, args: List[str], timeout: int = OPENCLI_TIMEOUT) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.command] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=os.environ,
            )
        except subprocess.TimeoutExpired as exc:
            raise OpenCliBackendError("opencli_timeout", f"OpenCLI command timed out: {exc}") from exc

    def _list_browser_tabs(self) -> Optional[Dict[str, str]]:
        if not get_opencli_close_tabs_after_run():
            return {}

        result = self._run_command(["browser", "tab", "list"], timeout=OPENCLI_TAB_COMMAND_TIMEOUT)
        if result.returncode != 0:
            logging.warning("opencli browser tab list failed: %s", (result.stderr or result.stdout).strip()[:200])
            return None

        try:
            payload = json.loads(result.stdout or "null")
        except json.JSONDecodeError:
            logging.warning("opencli browser tab list returned invalid JSON")
            return None
        return extract_opencli_browser_tabs(payload)

    def _cleanup_new_browser_tabs(self, before_tabs: Optional[Dict[str, str]]) -> None:
        if before_tabs is None or not get_opencli_close_tabs_after_run():
            return

        for _ in range(8):
            after_tabs = self._list_browser_tabs()
            if after_tabs is None:
                return

            pending_targets = [
                target_id
                for target_id, url in after_tabs.items()
                if target_id not in before_tabs and is_twitter_browser_tab(url)
            ]
            if not pending_targets:
                return

            for target_id in pending_targets:
                result = self._run_command(
                    ["browser", "tab", "close", target_id],
                    timeout=OPENCLI_TAB_COMMAND_TIMEOUT,
                )
                if result.returncode != 0:
                    logging.warning(
                        "opencli browser tab close failed: %s",
                        (result.stderr or result.stdout).strip()[:200],
                    )
            time.sleep(0.5)

        logging.warning(
            "opencli cleanup left browser tabs unmatched after retries, running browser-close fallback",
        )

    def _release_browser_lease(self) -> None:
        if not get_opencli_close_tabs_after_run():
            return

        result = self._run_command(["browser", "close"], timeout=OPENCLI_TAB_COMMAND_TIMEOUT)
        if result.returncode != 0:
            logging.warning("opencli browser close failed: %s", (result.stderr or result.stdout).strip()[:200])

    def _verify_capability(self) -> None:
        result = self._run_command(["twitter", "tweets", "--help"], timeout=30)
        if result.returncode != 0:
            raise OpenCliBackendError(
                "opencli_capability_missing",
                (result.stderr or result.stdout or "OpenCLI does not expose the twitter tweets command.").strip(),
            )

        help_text = f"{result.stdout}\n{result.stderr}".lower()
        if "twitter tweets" not in help_text:
            raise OpenCliBackendError(
                "opencli_capability_missing",
                "OpenCLI does not expose the twitter tweets command.",
            )

    def _run_doctor(self) -> None:
        result = self._run_command(["doctor"], timeout=30)
        if result.returncode == 0:
            return
        code = _classify_opencli_failure(result.returncode, result.stderr)
        if code in {"opencli_browser_unavailable", "opencli_auth_required"}:
            raise OpenCliBackendError(code, result.stderr.strip() or "opencli doctor failed")
        logging.warning(f"opencli doctor returned {result.returncode}: {(result.stderr or result.stdout).strip()[:200]}")

    def _parse_tweets_output(self, stdout: str, source: Dict[str, Any], cutoff: datetime) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(stdout or "null")
        except json.JSONDecodeError as exc:
            raise OpenCliBackendError("opencli_parse_error", "opencli twitter tweets returned invalid JSON") from exc

        articles = []
        for record in extract_opencli_tweet_records(payload):
            article = normalize_opencli_tweet(record, source, cutoff)
            if article:
                articles.append(article)
        return articles

    def _fetch_user_tweets(self, source: Dict[str, Any], cutoff: datetime) -> Dict[str, Any]:
        handle = source["handle"].lstrip("@")
        result = self._run_command(
            [
                "twitter",
                "tweets",
                handle,
                "--limit",
                str(MAX_TWEETS_PER_USER),
                "-f",
                "json",
            ]
        )

        if result.returncode != 0:
            code = _classify_opencli_failure(result.returncode, result.stderr)
            message = (result.stderr or result.stdout or "opencli twitter tweets failed").strip()
            if code in OPENCLI_GLOBAL_ERROR_CODES:
                raise OpenCliBackendError(code, message)
            return self._make_error(source, f"{code}: {message[:160]}", 0)

        articles = self._parse_tweets_output(result.stdout, source, cutoff)
        return self._make_result(source, articles, 0)

    def fetch_all(self, sources: List[Dict[str, Any]], cutoff: datetime) -> List[Dict[str, Any]]:
        if not sources:
            return []

        before_chrome_windows = self._before_chrome_windows
        before_tabs = self._list_browser_tabs()
        results: List[Dict[str, Any]] = []
        total = len(sources)

        try:
            first = self._fetch_user_tweets(sources[0], cutoff)
            results.append(first)
            logging.info(f"[1/{total}] @{first['handle']}: {first['count']} tweets via OpenCLI")

            remaining = sources[1:]
            if not remaining:
                return results

            done = 1
            max_workers = self._max_workers if self._max_workers is not None else get_opencli_max_workers()
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self._fetch_user_tweets, source, cutoff): source for source in remaining}
                for future in as_completed(futures):
                    source = futures[future]
                    try:
                        result = future.result()
                    except OpenCliBackendError as exc:
                        result = self._make_error(source, f"{exc.code}: {exc.message[:160]}", 0)
                    results.append(result)
                    done += 1
                    if result["status"] == "ok":
                        logging.info(f"[{done}/{total}] ✅ @{result['handle']}: {result['count']} tweets via OpenCLI")
                    else:
                        logging.warning(f"[{done}/{total}] ❌ @{result['handle']}: {result.get('error', 'unknown')}")

            return results
        finally:
            self._cleanup_new_browser_tabs(before_tabs)
            self._release_browser_lease()
            cleanup_new_opencli_chrome_windows(before_chrome_windows)


class OfficialBackend(TwitterBackend):
    """Official X API v2 backend (existing logic)."""

    def __init__(self, bearer_token: str, no_cache: bool = False):
        self.bearer_token = bearer_token
        self.no_cache = no_cache

    # -- ID cache helpers --

    @staticmethod
    def _load_id_cache() -> Dict[str, Any]:
        try:
            with open(ID_CACHE_PATH, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_id_cache(cache: Dict[str, Any]) -> None:
        try:
            with open(ID_CACHE_PATH, 'w') as f:
                json.dump(cache, f)
        except Exception as e:
            logging.warning(f"Failed to save ID cache: {e}")

    def _batch_resolve_user_ids(self, handles: List[str]) -> Dict[str, str]:
        now = time.time()
        cache = {} if self.no_cache else self._load_id_cache()
        ttl_seconds = ID_CACHE_TTL_DAYS * 86400

        result: Dict[str, str] = {}
        to_resolve: List[str] = []
        for handle in handles:
            key = handle.lower()
            entry = cache.get(key)
            if entry and (now - entry.get("ts", 0)) < ttl_seconds:
                result[key] = entry["id"]
            else:
                to_resolve.append(handle)

        if to_resolve:
            logging.info(f"Batch resolving {len(to_resolve)} usernames (cached: {len(result)})")
            headers = {
                "Authorization": f"Bearer {self.bearer_token}",
                "User-Agent": "FollowNews/2.0"
            }
            for i in range(0, len(to_resolve), 100):
                batch = to_resolve[i:i+100]
                url = f"{USER_LOOKUP_ENDPOINT}?{urlencode({'usernames': ','.join(batch)})}"
                try:
                    req = Request(url, headers=headers)
                    with urlopen(req, timeout=TIMEOUT) as resp:
                        data = json.loads(resp.read().decode())

                    if 'data' in data:
                        for user in data['data']:
                            key = user['username'].lower()
                            result[key] = user['id']
                            cache[key] = {"id": user['id'], "ts": now}

                    if 'errors' in data:
                        for err in data['errors']:
                            logging.warning(f"User lookup error: {err.get('detail', err)}")

                except Exception as e:
                    logging.error(f"Batch user lookup failed: {e}")
                    for handle in batch:
                        try:
                            fallback_url = f"{USER_LOOKUP_ENDPOINT}?{urlencode({'usernames': handle})}"
                            req = Request(fallback_url, headers=headers)
                            with urlopen(req, timeout=TIMEOUT) as resp:
                                fallback_data = json.loads(resp.read().decode())
                            if 'data' in fallback_data and fallback_data['data']:
                                key = handle.lower()
                                result[key] = fallback_data['data'][0]['id']
                                cache[key] = {"id": result[key], "ts": now}
                        except Exception as e2:
                            logging.warning(f"Individual lookup failed for @{handle}: {e2}")

            if not self.no_cache:
                self._save_id_cache(cache)
        else:
            logging.info(f"All {len(result)} usernames resolved from cache")

        return result

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        try:
            if date_str.endswith('Z'):
                date_str = date_str[:-1] + '+00:00'
            return datetime.fromisoformat(date_str)
        except (ValueError, TypeError):
            logging.debug(f"Failed to parse Twitter date: {date_str}")
            return None

    def _fetch_user_tweets(self, source: Dict[str, Any], cutoff: datetime,
                           user_id: Optional[str] = None) -> Dict[str, Any]:
        handle = source["handle"].lstrip('@')
        topics = source["topics"]

        for attempt in range(RETRY_COUNT + 1):
            try:
                params = {
                    "max_results": min(MAX_TWEETS_PER_USER, 100),
                    "tweet.fields": "created_at,public_metrics,context_annotations,referenced_tweets",
                    "expansions": "author_id",
                    "user.fields": "verified,public_metrics"
                }

                if not user_id:
                    user_url = f"{USER_LOOKUP_ENDPOINT}?{urlencode({'usernames': handle})}"
                    headers = {
                        "Authorization": f"Bearer {self.bearer_token}",
                        "User-Agent": "FollowNews/2.0"
                    }
                    req = Request(user_url, headers=headers)
                    with urlopen(req, timeout=TIMEOUT) as resp:
                        user_data = json.loads(resp.read().decode())
                    if 'data' not in user_data or not user_data['data']:
                        raise ValueError(f"User not found: {handle}")
                    user_id = user_data['data'][0]['id']

                headers = {
                    "Authorization": f"Bearer {self.bearer_token}",
                    "User-Agent": "FollowNews/2.0"
                }

                time.sleep(0.3)

                tweets_url = f"{OFFICIAL_API_BASE}/users/{user_id}/tweets?{urlencode(params)}"
                req = Request(tweets_url, headers=headers)

                with urlopen(req, timeout=TIMEOUT) as resp:
                    tweets_data = json.loads(resp.read().decode())

                articles = []
                if 'data' in tweets_data:
                    for tweet in tweets_data['data']:
                        created_at = self._parse_date(tweet.get('created_at', ''))
                        if not created_at or created_at < cutoff:
                            continue

                        text = tweet.get('text', '')
                        if text.startswith('RT @'):
                            continue
                        referenced = tweet.get('referenced_tweets', [])
                        if any(ref.get('type') == 'replied_to' for ref in referenced):
                            continue

                        articles.append({
                            "title": clean_tweet_text(text),
                            "link": f"https://twitter.com/{handle}/status/{tweet['id']}",
                            "date": created_at.isoformat(),
                            "topics": topics[:],
                            "metrics": tweet.get('public_metrics', {}),
                            "tweet_id": tweet['id']
                        })

                return self._make_result(source, articles, attempt)

            except HTTPError as e:
                if e.code == 429:
                    error_msg = "Rate limit exceeded"
                    logging.warning(f"Rate limit hit for @{handle}, attempt {attempt + 1}")
                    if attempt < RETRY_COUNT:
                        time.sleep(60)
                        continue
                else:
                    error_msg = f"HTTP {e.code}: {e.reason}"

            except Exception as e:
                error_msg = str(e)[:100]
                logging.debug(f"Attempt {attempt + 1} failed for @{handle}: {error_msg}")

            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue

            return self._make_error(source, error_msg, attempt)

    def fetch_all(self, sources: List[Dict[str, Any]], cutoff: datetime) -> List[Dict[str, Any]]:
        all_handles = [s["handle"].lstrip('@') for s in sources]
        user_id_map = self._batch_resolve_user_ids(all_handles)

        results: List[Dict[str, Any]] = []
        total = len(sources)
        done = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {}
            for source in sources:
                handle = source["handle"].lstrip('@')
                resolved_id = user_id_map.get(handle.lower())
                futures[pool.submit(self._fetch_user_tweets, source, cutoff, resolved_id)] = source

            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                done += 1
                if result["status"] == "ok":
                    logging.info(f"[{done}/{total}] ✅ @{result['handle']}: {result['count']} tweets"
                                 + (f" (top: {result['articles'][0]['metrics']['like_count']}❤️)" if result.get('articles') else ""))
                else:
                    logging.warning(f"[{done}/{total}] ❌ @{result['handle']}: {result.get('error','unknown')}")

        return results


class TwitterApiIoBackend(TwitterBackend):
    """twitterapi.io backend."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._limiter = RateLimiter(qps=5)

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Parse twitterapi.io date format: 'Tue Dec 10 07:00:30 +0000 2024'."""
        try:
            return datetime.strptime(date_str, "%a %b %d %H:%M:%S %z %Y")
        except (ValueError, TypeError):
            logging.debug(f"Failed to parse twitterapi.io date: {date_str}")
            return None

    def _parse_tweets_page(self, tweets: list, handle: str, topics: list, cutoff: datetime) -> list:
        """Parse a page of tweets into article dicts."""
        articles = []
        for tweet in tweets:
            # Skip retweets
            if tweet.get("retweeted_tweet"):
                continue
            created_at = self._parse_date(tweet.get("createdAt", ""))
            if not created_at or created_at < cutoff:
                continue

            text = tweet.get("text", "")
            if text.startswith("RT @"):
                continue

            tweet_id = tweet.get("id", "")
            link = tweet.get("url") or f"https://twitter.com/{handle}/status/{tweet_id}"

            articles.append({
                "title": clean_tweet_text(text),
                "link": link,
                "date": created_at.isoformat(),
                "topics": topics[:],
                "metrics": {
                    "like_count": tweet.get("likeCount", 0),
                    "retweet_count": tweet.get("retweetCount", 0),
                    "reply_count": tweet.get("replyCount", 0),
                    "quote_count": tweet.get("quoteCount", 0),
                    "impression_count": tweet.get("viewCount", 0),
                },
                "tweet_id": tweet_id,
            })
        return articles

    def _fetch_user_tweets(self, source: Dict[str, Any], cutoff: datetime) -> Dict[str, Any]:
        handle = source["handle"].lstrip('@')
        topics = source["topics"]

        for attempt in range(RETRY_COUNT + 1):
            try:
                params = urlencode({
                    "userName": handle,
                    "includeReplies": "false",
                })
                url = f"{TWITTERAPIIO_BASE}/twitter/user/last_tweets?{params}"
                headers = {
                    "X-API-Key": self.api_key,
                    "User-Agent": "FollowNews/2.0",
                }

                self._limiter.wait()

                req = Request(url, headers=headers)
                with urlopen(req, timeout=TIMEOUT) as resp:
                    raw = json.loads(resp.read().decode())

                # API wraps response in {"data": {...}} envelope
                data = raw.get("data", raw)

                articles = self._parse_tweets_page(
                    data.get("tweets", []), handle, topics, cutoff
                )

                # Pagination: fetch one more page if available and all tweets still in window
                has_next = data.get("has_next_page", False)
                next_cursor = data.get("next_cursor")
                if has_next and next_cursor and articles:
                    oldest = min(a["date"] for a in articles)
                    if oldest >= cutoff.isoformat():
                        self._limiter.wait()
                        page2_params = urlencode({
                            "userName": handle,
                            "includeReplies": "false",
                            "cursor": next_cursor,
                        })
                        page2_url = f"{TWITTERAPIIO_BASE}/twitter/user/last_tweets?{page2_params}"
                        req2 = Request(page2_url, headers=headers)
                        with urlopen(req2, timeout=TIMEOUT) as resp2:
                            raw2 = json.loads(resp2.read().decode())
                        data2 = raw2.get("data", raw2)
                        articles.extend(self._parse_tweets_page(
                            data2.get("tweets", []), handle, topics, cutoff
                        ))
                        has_next = data2.get("has_next_page", False)

                # Truncation warning
                if has_next and articles:
                    oldest = min(a["date"] for a in articles)
                    if oldest >= cutoff.isoformat():
                        logging.warning(f"@{handle}: results may be truncated ({len(articles)} tweets, more available)")

                return self._make_result(source, articles, attempt)

            except HTTPError as e:
                if e.code == 429:
                    error_msg = "Rate limit exceeded"
                    logging.warning(f"Rate limit hit for @{handle}, attempt {attempt + 1}")
                    if attempt < RETRY_COUNT:
                        time.sleep(5)
                        continue
                else:
                    error_msg = f"HTTP {e.code}: {e.reason}"

            except Exception as e:
                error_msg = str(e)[:100]
                logging.debug(f"Attempt {attempt + 1} failed for @{handle}: {error_msg}")

            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue

            return self._make_error(source, error_msg, attempt)

    def fetch_all(self, sources: List[Dict[str, Any]], cutoff: datetime) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        total = len(sources)
        done = 0
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(self._fetch_user_tweets, source, cutoff): source
                       for source in sources}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                done += 1
                if result["status"] == "ok":
                    logging.info(f"[{done}/{total}] ✅ @{result['handle']}: {result['count']} tweets"
                                 + (f" (top: {result['articles'][0]['metrics']['like_count']}❤️)" if result['articles'] else ""))
                else:
                    logging.warning(f"[{done}/{total}] ❌ @{result['handle']}: {result['error']}")

        return results


class GetXApiBackend(TwitterBackend):
    """GetXAPI backend."""

    def __init__(self, api_key: str):
        """Initialize GetXAPI backend with API key validation."""
        if not api_key or len(api_key) < 10:
            raise ValueError("Invalid GETX_API_KEY format - expected at least 10 characters")
        self.api_key = api_key
        self.logger = logging.getLogger("fetch-twitter")

    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse GetXAPI date string with multiple format support.
        
        Supported formats:
        - 'Tue Dec 10 07:00:30 +0000 2024' (Twitter format)
        - '2024-12-10T07:00:30+00:00' (ISO 8601)
        - '2024-12-10 07:00:30' (Simple datetime)
        """
        formats = [
            "%a %b %d %H:%M:%S %z %Y",      # Twitter format
            "%Y-%m-%dT%H:%M:%S%z",          # ISO 8601
            "%Y-%m-%d %H:%M:%S",            # Simple datetime
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except (ValueError, TypeError):
                continue
        
        self.logger.debug(f"Failed to parse date '{date_str}' with all known formats")
        return None

    def _parse_tweets_page(self, tweets: list, handle: str, topics: list, cutoff: datetime) -> list:
        articles = []
        for tweet in tweets:
            tweet_id = tweet.get("id")
            text = tweet.get("text")
            created_at_raw = tweet.get("createdAt")
            if not tweet_id or not text or not created_at_raw:
                continue
            if tweet.get("isReply"):
                continue
            if text.startswith("RT @"):
                continue

            created_at = self._parse_date(created_at_raw)
            if not created_at or created_at < cutoff:
                continue

            link = tweet.get("url") or f"https://x.com/{handle}/status/{tweet_id}"

            articles.append({
                "title": clean_tweet_text(text),
                "link": link,
                "date": created_at.isoformat(),
                "topics": topics[:],
                "metrics": {
                    "like_count": tweet.get("likeCount", 0),
                    "retweet_count": tweet.get("retweetCount", 0),
                    "reply_count": tweet.get("replyCount", 0),
                    "quote_count": tweet.get("quoteCount", 0),
                    "impression_count": tweet.get("viewCount", 0),
                },
                "tweet_id": tweet_id,
            })
        return articles

    def _fetch_user_tweets(self, source: Dict[str, Any], cutoff: datetime) -> Dict[str, Any]:
        handle = source["handle"].lstrip('@')
        topics = source["topics"]

        for attempt in range(RETRY_COUNT + 1):
            try:
                url = f"{GETXAPI_BASE}/twitter/user/tweets?{urlencode({'userName': handle})}"
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "FollowNews/2.0",
                }

                req = Request(url, headers=headers)
                with urlopen(req, timeout=TIMEOUT) as resp:
                    raw = json.loads(resp.read().decode())

                if raw.get("error"):
                    return self._make_error(source, str(raw["error"])[:100], attempt)

                articles = self._parse_tweets_page(
                    raw.get("tweets", []), handle, topics, cutoff
                )

                has_more = raw.get("has_more", False)
                next_cursor = raw.get("next_cursor")
                
                # Fetch page 2 if more results available (with retry)
                if has_more and next_cursor and articles:
                    oldest = min(datetime.fromisoformat(a["date"]) for a in articles)
                    if oldest >= cutoff:
                        for page_attempt in range(RETRY_COUNT + 1):
                            try:
                                page2_url = f"{GETXAPI_BASE}/twitter/user/tweets?{urlencode({'userName': handle, 'cursor': next_cursor})}"
                                req2 = Request(page2_url, headers=headers)
                                with urlopen(req2, timeout=TIMEOUT) as resp2:
                                    raw2 = json.loads(resp2.read().decode())
                                if raw2.get("error"):
                                    raise ValueError(str(raw2["error"])[:100])
                                articles.extend(self._parse_tweets_page(
                                    raw2.get("tweets", []), handle, topics, cutoff
                                ))
                                has_more = raw2.get("has_more", False)
                                break  # Success
                            except Exception as e:
                                self.logger.warning(f"@{handle}: page 2 attempt {page_attempt + 1} failed: {e}")
                                if page_attempt < RETRY_COUNT:
                                    time.sleep(RETRY_DELAY * (2 ** page_attempt))
                                else:
                                    self.logger.warning(f"@{handle}: page 2 failed after {RETRY_COUNT} attempts, keeping page 1 results")
                                    has_more = False

                if has_more and articles:
                    oldest = min(datetime.fromisoformat(a["date"]) for a in articles)
                    if oldest >= cutoff:
                        logging.warning(f"@{handle}: results may be truncated ({len(articles)} tweets, more available)")

                return self._make_result(source, articles, attempt)

            except HTTPError as e:
                if e.code == 429:
                    error_msg = "Rate limit exceeded"
                    logging.warning(f"Rate limit hit for @{handle}, attempt {attempt + 1}")
                    if attempt < RETRY_COUNT:
                        time.sleep(5)
                        continue
                else:
                    error_msg = f"HTTP {e.code}: {e.reason}"

            except Exception as e:
                error_msg = str(e)[:100]
                logging.debug(f"Attempt {attempt + 1} failed for @{handle}: {error_msg}")

            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue

            return self._make_error(source, error_msg, attempt)

    def fetch_all(self, sources: List[Dict[str, Any]], cutoff: datetime) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        total = len(sources)
        done = 0
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(self._fetch_user_tweets, source, cutoff): source
                       for source in sources}
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                done += 1
                if result["status"] == "ok":
                    logging.info(f"[{done}/{total}] ✅ @{result['handle']}: {result['count']} tweets"
                                 + (f" (top: {result['articles'][0]['metrics']['like_count']}❤️)" if result['articles'] else ""))
                else:
                    logging.warning(f"[{done}/{total}] ❌ @{result['handle']}: {result['error']}")

        return results


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def _instantiate_backend(
    backend_name: str,
    no_cache: bool = False,
    opencli_workers: Optional[int] = None,
) -> Optional[TwitterBackend]:
    """Instantiate one backend without applying fallback policy."""
    if backend_name == "opencli":
        logging.info("Using OpenCLI backend")
        return OpenCliBackend(max_workers=opencli_workers)

    if backend_name == "getxapi":
        key = os.getenv("GETX_API_KEY")
        if not key:
            logging.info("GETX_API_KEY not set; getxapi backend unavailable")
            return None
        logging.info("Using GetXAPI backend")
        return GetXApiBackend(key)

    if backend_name == "twitterapiio":
        key = os.getenv("TWITTERAPI_IO_KEY")
        if not key:
            logging.info("TWITTERAPI_IO_KEY not set; twitterapi.io backend unavailable")
            return None
        logging.info("Using twitterapi.io backend")
        return TwitterApiIoBackend(key)

    if backend_name == "official":
        token = os.getenv("X_BEARER_TOKEN")
        if not token:
            logging.info("X_BEARER_TOKEN not set; official backend unavailable")
            return None
        logging.info("Using official X API v2 backend")
        return OfficialBackend(token, no_cache=no_cache)

    logging.error(f"Unknown backend: {backend_name}")
    return None


def fetch_with_backend_chain(
    backend_name: str,
    sources: List[Dict[str, Any]],
    cutoff: datetime,
    no_cache: bool = False,
    opencli_workers: Optional[int] = None,
) -> Tuple[str, List[Dict[str, Any]], List[Dict[str, str]]]:
    """Fetch Twitter data using explicit backend or auto fallback chain."""
    diagnostics: List[Dict[str, str]] = []
    explicit = backend_name != "auto"

    for candidate in get_backend_order(backend_name):
        try:
            backend = _instantiate_backend(candidate, no_cache=no_cache, opencli_workers=opencli_workers)
        except OpenCliBackendError as exc:
            diagnostics.append({"backend": candidate, "code": exc.code, "message": exc.message})
            logging.warning(f"{candidate} unavailable: {exc.code}: {exc.message}")
            if explicit:
                return candidate, [], diagnostics
            continue

        if backend is None:
            diagnostics.append({
                "backend": candidate,
                "code": "backend_unavailable",
                "message": f"{candidate} backend is not configured",
            })
            if explicit:
                return candidate, [], diagnostics
            continue

        try:
            return candidate, backend.fetch_all(sources, cutoff), diagnostics
        except OpenCliBackendError as exc:
            diagnostics.append({"backend": candidate, "code": exc.code, "message": exc.message})
            logging.warning(f"{candidate} failed globally: {exc.code}: {exc.message}")
            if explicit:
                return candidate, [], diagnostics
            continue

    return backend_name, [], diagnostics


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def load_twitter_sources(defaults_dir: Path, config_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load Twitter sources from unified configuration with overlay support."""
    try:
        from config_loader import load_merged_sources
    except ImportError:
        # Fallback for relative import
        import sys
        sys.path.append(str(Path(__file__).parent))
        from config_loader import load_merged_sources

    # Load merged sources from defaults + optional user overlay
    all_sources = load_merged_sources(defaults_dir, config_dir)

    # Filter Twitter sources that are enabled
    twitter_sources = []
    for source in all_sources:
        if source.get("type") == "twitter" and source.get("enabled", True):
            if not source.get("handle"):
                logging.warning(f"Twitter source {source.get('id')} missing handle, skipping")
                continue
            twitter_sources.append(source)

    logging.info(f"Loaded {len(twitter_sources)} enabled Twitter sources")
    return twitter_sources


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Main Twitter fetching function."""
    parser = argparse.ArgumentParser(
        description="Fetch recent tweets from Twitter/X KOL accounts. "
                   "Supports OpenCLI, GetXAPI, twitterapi.io, and official X API v2 backends.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    export X_BEARER_TOKEN="your_token_here"
    python3 fetch-twitter.py
    python3 fetch-twitter.py --defaults config/defaults --config workspace/config --hours 24 -o results.json
    python3 fetch-twitter.py --backend twitterapiio  # use twitterapi.io
    python3 fetch-twitter.py --config workspace/config --verbose  # backward compatibility
        """
    )

    parser.add_argument(
        "--defaults",
        type=Path,
        default=Path("config/defaults"),
        help="Default configuration directory with skill defaults (default: config/defaults)"
    )

    parser.add_argument(
        "--config",
        type=Path,
        help="User configuration directory for overlays (optional)"
    )

    parser.add_argument(
        "--hours",
        type=int,
        default=48,
        help="Time window in hours for tweets (default: 48)"
    )

    parser.add_argument(
        "--output", "-o",
        type=Path,
        help="Output JSON path (default: auto-generated temp file)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass username→ID cache (official backend only)"
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-fetch even if cached output exists"
    )

    parser.add_argument(
        "--backend",
        choices=["opencli", "official", "twitterapiio", "getxapi", "auto"],
        default=None,
        help="Twitter backend (overrides TWITTER_API_BACKEND env var). "
             "auto = opencli first, then getxapi, twitterapiio, official"
    )

    parser.add_argument(
        "--opencli-workers",
        type=int,
        default=None,
        help="OpenCLI concurrency for twitter tweets (1-10). "
             f"Defaults to {OPENCLI_DEFAULT_MAX_WORKERS}.",
    )

    args = parser.parse_args()
    logger = setup_logging(args.verbose)

    # Resume support: skip if output exists, is valid JSON, and < 1 hour old
    if args.output and args.output.exists() and not args.force:
        try:
            age_seconds = time.time() - args.output.stat().st_mtime
            if age_seconds < 3600:
                with open(args.output, 'r') as f:
                    json.load(f)
                logger.info(f"Skipping (cached output exists): {args.output}")
                return 0
        except (json.JSONDecodeError, OSError):
            pass

    # Resolve backend: CLI arg > env var > default (auto)
    backend_name = args.backend or os.getenv("TWITTER_API_BACKEND", "auto")

    # Auto-generate unique output path if not specified
    if not args.output:
        fd, temp_path = tempfile.mkstemp(prefix="follow-news-twitter-", suffix=".json")
        os.close(fd)
        args.output = Path(temp_path)

    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)

        # Backward compatibility: if only --config provided, use old behavior
        if args.config and args.defaults == Path("config/defaults") and not args.defaults.exists():
            logger.debug("Backward compatibility mode: using --config as sole source")
            sources = load_twitter_sources(args.config, None)
        else:
            sources = load_twitter_sources(args.defaults, args.config)

        if not sources:
            logger.warning("No Twitter sources found or all disabled")

        logger.info(f"Fetching {len(sources)} Twitter accounts (window: {args.hours}h, backend: {backend_name})")

        selected_backend, results, backend_diagnostics = fetch_with_backend_chain(
            backend_name,
            sources,
            cutoff,
            no_cache=args.no_cache,
            opencli_workers=args.opencli_workers,
        )

        if not results:
            logger.warning("No Twitter backend available. Writing empty result and skipping Twitter fetch.")
            empty_result = {
                "generated": datetime.now(timezone.utc).isoformat(),
                "source_type": "twitter",
                "backend": selected_backend,
                "hours": args.hours,
                "sources_total": 0,
                "sources_ok": 0,
                "total_articles": 0,
                "sources": [],
                "skipped_reason": build_twitter_skipped_reason(backend_name, backend_diagnostics),
                "backend_diagnostics": backend_diagnostics,
            }
            with open(args.output, "w", encoding='utf-8') as f:
                json.dump(empty_result, f, ensure_ascii=False, indent=2)
            print(f"Output (empty): {args.output}")
            return 0

        # Sort: priority first, then by article count
        results.sort(key=lambda x: (not x.get("priority", False), -x.get("count", 0)))

        ok_count = sum(1 for r in results if r["status"] == "ok")
        total_tweets = sum(r.get("count", 0) for r in results)

        output = {
            "generated": datetime.now(timezone.utc).isoformat(),
            "source_type": "twitter",
            "backend": selected_backend,
            "requested_backend": backend_name,
            "backend_diagnostics": backend_diagnostics,
            "defaults_dir": str(args.defaults),
            "config_dir": str(args.config) if args.config else None,
            "hours": args.hours,
            "sources_total": len(results),
            "sources_ok": ok_count,
            "total_articles": total_tweets,
            "sources": results,
        }

        # Write output
        json_str = json.dumps(output, ensure_ascii=False, indent=2)
        with open(args.output, "w", encoding='utf-8') as f:
            f.write(json_str)

        logger.info(f"✅ Done: {ok_count}/{len(results)} accounts ok, "
                   f"{total_tweets} tweets → {args.output}")

        return 0

    except Exception as e:
        logger.error(f"💥 Twitter fetch failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
