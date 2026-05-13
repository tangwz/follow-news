#!/usr/bin/env python3
"""
Tiny local editor backend for follow-news config JSON files.

Usage:
  python3 tools/config-editor/server.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Dict, Any
from urllib.parse import parse_qs, urlparse


BASE_DIR = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).resolve().parent
ALLOWED_FILES = {
    "sources": {
        "path": BASE_DIR / "config" / "defaults" / "sources.json",
        "label_zh": "默认订阅源配置",
        "label_en": "Default sources config",
    },
    "topics": {
        "path": BASE_DIR / "config" / "defaults" / "topics.json",
        "label_zh": "默认话题配置",
        "label_en": "Default topics config",
    },
}


class ConfigEditorHandler(SimpleHTTPRequestHandler):
    _json_prefix = re.compile(r"^/api/")
    _LOCAL_ORIGINS = {"127.0.0.1", "localhost", "::1"}
    _ALLOWED_SOURCE_TYPES = {"rss", "twitter", "web", "github", "reddit"}

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _api_error(self, status: int, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status=status)

    def _read_request_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)

    def _validate_key(self) -> str:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        key = (qs.get("key") or [None])[0]
        if not key or key not in ALLOWED_FILES:
            raise ValueError("unknown config key")
        return key

    def _is_allowed_write_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return False

        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False

        if parsed.port is None:
            normalized_port = {"http": 80, "https": 443}.get(parsed.scheme)
        else:
            normalized_port = parsed.port
        if normalized_port is None:
            return False

        server_port = self.server.server_address[1]
        if normalized_port != server_port:
            return False

        return parsed.hostname in self._LOCAL_ORIGINS

    def _send_cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self._is_allowed_write_origin():
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _validate_sources_payload(self, sources: Any) -> None:
        if not isinstance(sources, list):
            raise ValueError("'sources' should be a list")

        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise ValueError(f"Source at index {index} should be an object")

            source_id = source.get("id", f"index:{index}")
            missing = []
            for field in ("id", "type", "name", "enabled", "priority", "topics"):
                if field not in source:
                    missing.append(field)
            if missing:
                raise ValueError(
                    f"Source '{source_id}' missing required field(s): {', '.join(missing)}"
                )

            source_type = source["type"]
            if not isinstance(source_type, str) or source_type not in self._ALLOWED_SOURCE_TYPES:
                raise ValueError(f"Source '{source_id}' has unsupported type: {source_type}")

            if not isinstance(source["id"], str) or not source["id"].strip():
                raise ValueError(f"Source at index {index} has invalid 'id'")
            if not isinstance(source["name"], str) or not source["name"].strip():
                raise ValueError(f"Source '{source_id}' has invalid 'name'")
            if not isinstance(source["enabled"], bool):
                raise ValueError(f"Source '{source_id}' has invalid 'enabled' value")
            if not isinstance(source["priority"], bool):
                raise ValueError(f"Source '{source_id}' has invalid 'priority' value")

            topics = source["topics"]
            if not isinstance(topics, list) or not all(isinstance(topic, str) for topic in topics):
                raise ValueError(f"Source '{source_id}' has invalid 'topics'; expected string array")

            if source_type == "rss":
                if not isinstance(source.get("url"), str) or not source["url"].strip():
                    raise ValueError(f"Source '{source_id}' missing required field 'url'")
            elif source_type == "twitter":
                if not isinstance(source.get("handle"), str) or not source["handle"].strip():
                    raise ValueError(f"Source '{source_id}' missing required field 'handle'")
            elif source_type == "github":
                if not isinstance(source.get("repo"), str) or not source["repo"].strip():
                    raise ValueError(f"Source '{source_id}' missing required field 'repo'")
            elif source_type == "reddit":
                if not isinstance(source.get("subreddit"), str) or not source["subreddit"].strip():
                    raise ValueError(f"Source '{source_id}' missing required field 'subreddit'")

    def _validate_topics_payload(self, topics: Any) -> None:
        if not isinstance(topics, list):
            raise ValueError("'topics' should be a list")

        for index, topic in enumerate(topics):
            if not isinstance(topic, dict):
                raise ValueError(f"Topic at index {index} should be an object")

            topic_id = topic.get("id", f"index:{index}")
            missing = []
            for field in ("id", "search"):
                if field not in topic:
                    missing.append(field)
            if missing:
                raise ValueError(
                    f"Topic '{topic_id}' missing required field(s): {', '.join(missing)}"
                )

            if not isinstance(topic["id"], str) or not topic["id"].strip():
                raise ValueError(f"Topic '{topic_id}' has invalid 'id'")

            search = topic["search"]
            if not isinstance(search, dict):
                raise ValueError(f"Topic '{topic_id}' has invalid 'search' value")

            queries = search.get("queries")
            if "queries" not in search:
                raise ValueError(f"Topic '{topic_id}' missing required field 'search.queries'")
            if not isinstance(queries, list) or not all(isinstance(query, str) for query in queries):
                raise ValueError(f"Topic '{topic_id}' has invalid field 'search.queries'; expected string array")

    def do_OPTIONS(self) -> None:
        if not self.path.startswith("/api/"):
            return super().do_OPTIONS()

        if self.path.startswith("/api/file") and not self._is_allowed_write_origin():
            return self._api_error(403, "forbidden: cross-origin writes are not allowed")

        requested_method = (self.headers.get("Access-Control-Request-Method") or "").upper()
        allow_methods = "GET, OPTIONS"
        if requested_method == "POST" or self.path.startswith("/api/file"):
            allow_methods = "GET, POST, OPTIONS"

        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", allow_methods)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:
        if not self.path.startswith("/api/"):
            return super().do_GET()

        if self.path.startswith("/api/list"):
            payload = {
                "ok": True,
                "files": [
                    {
                        "key": key,
                        "path": str(info["path"].relative_to(BASE_DIR)),
                        "label_zh": info["label_zh"],
                        "label_en": info["label_en"],
                    }
                    for key, info in ALLOWED_FILES.items()
                ],
            }
            return self._send_json(payload)

        if self.path.startswith("/api/file"):
            try:
                key = self._validate_key()
            except ValueError:
                return self._api_error(400, "invalid file key")

            info = ALLOWED_FILES[key]
            path = info["path"]

            if not path.exists():
                return self._api_error(404, f"config file not found: {path}")

            with path.open("r", encoding="utf-8") as fp:
                content = json.load(fp)

            return self._send_json(
                {
                    "ok": True,
                    "key": key,
                    "path": str(path.relative_to(BASE_DIR)),
                    "content": content,
                    "pretty": json.dumps(content, ensure_ascii=False, indent=2),
                }
            )

        self._api_error(404, "not found")

    def do_POST(self) -> None:
        if not self.path.startswith("/api/file"):
            return self._api_error(404, "not found")

        if not self._is_allowed_write_origin():
            return self._api_error(403, "forbidden: cross-origin writes are not allowed")

        try:
            payload = self._read_request_json()
            if not isinstance(payload, dict):
                raise TypeError("invalid request body")
            key = payload.get("key")
            content = payload.get("content")
        except (json.JSONDecodeError, TypeError):
            return self._api_error(400, "invalid request body")

        if key not in ALLOWED_FILES:
            return self._api_error(400, "invalid file key")

        if content is None:
            return self._api_error(400, "missing content")

        try:
            path = ALLOWED_FILES[key]["path"]
            if key not in content:
                raise ValueError(f"missing top-level key '{key}'")
            if key == "sources":
                self._validate_sources_payload(content.get("sources"))
            if key == "topics":
                self._validate_topics_payload(content.get("topics"))

            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as fp:
                json.dump(content, fp, ensure_ascii=False, indent=2)
                fp.write("\n")
            self._send_json({"ok": True, "key": key, "message": "saved"}, 200)
            return
        except ValueError as exc:
            return self._api_error(400, str(exc))
        except OSError as exc:
            return self._api_error(500, str(exc))


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Config editor backend for follow-news")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8787, help="Bind port")
    return parser


def main() -> int:
    args = create_parser().parse_args()
    os.chdir(STATIC_DIR)
    httpd = HTTPServer((args.host, args.port), ConfigEditorHandler)
    print(f"Config editor started at http://{args.host}:{args.port}")
    print(f"Serving static files from: {STATIC_DIR}")
    print("Editable files:")
    for key, info in ALLOWED_FILES.items():
        print(f"  - {key}: {info['path']}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
