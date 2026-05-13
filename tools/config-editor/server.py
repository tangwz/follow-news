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

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
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

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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

        try:
            payload = self._read_request_json()
            key = payload.get("key")
            content = payload.get("content")
        except json.JSONDecodeError:
            return self._api_error(400, "invalid request body")

        if key not in ALLOWED_FILES:
            return self._api_error(400, "invalid file key")

        if content is None:
            return self._api_error(400, "missing content")

        try:
            path = ALLOWED_FILES[key]["path"]
            if key not in content:
                raise ValueError(f"missing top-level key '{key}'")
            if key == "sources" and not isinstance(content.get("sources"), list):
                raise ValueError("'sources' should be a list")
            if key == "topics" and not isinstance(content.get("topics"), list):
                raise ValueError("'topics' should be a list")

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
