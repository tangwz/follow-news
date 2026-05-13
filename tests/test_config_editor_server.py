#!/usr/bin/env python3
"""Tests for config editor save behavior."""

from __future__ import annotations

import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import importlib.util

def _load_config_editor_server() -> Any:
    server_path = Path(__file__).resolve().parents[1] / "tools" / "config-editor" / "server.py"
    spec = importlib.util.spec_from_file_location("config_editor_server", server_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load config editor server module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server_module = _load_config_editor_server()


def _get_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ConfigEditorServerTest(unittest.TestCase):
    def test_post_accepts_bound_host_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sources_path = Path(tmpdir) / "sources.json"
            topics_path = Path(tmpdir) / "topics.json"

            payload_source = {
                "sources": [
                    {
                        "id": "bound-host-test",
                        "type": "twitter",
                        "name": "Bound Host",
                        "enabled": True,
                        "priority": False,
                        "topics": [],
                        "handle": "boundhost",
                    }
                ]
            }
            sources_path.write_text(json.dumps(payload_source, ensure_ascii=False, indent=2), encoding="utf-8")
            topics_path.write_text("[]", encoding="utf-8")

            allowed_files = {
                "sources": {
                    "path": sources_path,
                    "label_zh": "测试源",
                    "label_en": "Test sources",
                },
                "topics": {
                    "path": topics_path,
                    "label_zh": "测试话题",
                    "label_en": "Test topics",
                },
            }

            port = _get_free_port()

            with patch.dict(server_module.ALLOWED_FILES, allowed_files):
                server = server_module.HTTPServer(("0.0.0.0", port), server_module.ConfigEditorHandler)
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                try:
                    time.sleep(0.05)

                    request_payload = {
                        "key": "sources",
                        "content": payload_source,
                    }
                    request = Request(
                        f"http://127.0.0.1:{port}/api/file",
                        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "Origin": f"http://0.0.0.0:{port}",
                        },
                    )
                    with urlopen(request, timeout=2.0) as response:
                        body = response.read().decode("utf-8")
                    data = json.loads(body)
                    self.assertEqual(data, {"ok": True, "key": "sources", "message": "saved"})

                finally:
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=1.0)

    def test_post_accepts_bound_host_via_lan_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sources_path = Path(tmpdir) / "sources.json"
            topics_path = Path(tmpdir) / "topics.json"

            payload_source = {
                "sources": [
                    {
                        "id": "bound-host-lan",
                        "type": "twitter",
                        "name": "Bound Host LAN",
                        "enabled": True,
                        "priority": False,
                        "topics": [],
                        "handle": "boundhostlan",
                    }
                ]
            }
            sources_path.write_text(json.dumps(payload_source, ensure_ascii=False, indent=2), encoding="utf-8")
            topics_path.write_text("[]", encoding="utf-8")

            allowed_files = {
                "sources": {
                    "path": sources_path,
                    "label_zh": "测试源",
                    "label_en": "Test sources",
                },
                "topics": {
                    "path": topics_path,
                    "label_zh": "测试话题",
                    "label_en": "Test topics",
                },
            }

            port = _get_free_port()

            with patch.dict(server_module.ALLOWED_FILES, allowed_files):
                server = server_module.HTTPServer(("0.0.0.0", port), server_module.ConfigEditorHandler)
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                try:
                    time.sleep(0.05)

                    request_payload = {
                        "key": "sources",
                        "content": payload_source,
                    }
                    request = Request(
                        f"http://127.0.0.1:{port}/api/file",
                        data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                        method="POST",
                        headers={
                            "Content-Type": "application/json",
                            "Origin": f"http://192.168.1.12:{port}",
                        },
                    )
                    with urlopen(request, timeout=2.0) as response:
                        body = response.read().decode("utf-8")
                    data = json.loads(body)
                    self.assertEqual(data, {"ok": True, "key": "sources", "message": "saved"})

                finally:
                    server.shutdown()
                    server.server_close()
                    server_thread.join(timeout=1.0)

    def test_post_rejects_non_exact_file_route(self) -> None:
        port = _get_free_port()
        server = server_module.HTTPServer(("127.0.0.1", port), server_module.ConfigEditorHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            time.sleep(0.05)
            request = Request(f"http://127.0.0.1:{port}/api/filezzz", method="POST")
            request.add_header("Origin", f"http://127.0.0.1:{port}")
            with self.assertRaises(HTTPError) as context:
                urlopen(request, timeout=2.0)
            self.assertEqual(context.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
