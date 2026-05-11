#!/usr/bin/env python3
"""Tests for the OpenCLI Twitter backend."""

import json
import os
import subprocess
import sys
import unittest
import importlib.util
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from unittest.mock import mock_open

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

spec = importlib.util.spec_from_file_location("fetch_twitter", SCRIPTS_DIR / "fetch-twitter.py")
fetch_twitter = importlib.util.module_from_spec(spec)
sys.modules["fetch_twitter"] = fetch_twitter
spec.loader.exec_module(fetch_twitter)


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TestOpenCliTweetNormalization(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "sama-twitter",
            "type": "twitter",
            "name": "Sam Altman",
            "handle": "sama",
            "enabled": True,
            "priority": True,
            "topics": ["llm", "ai-agent"],
        }
        self.cutoff = utc("2026-05-08T00:00:00Z")

    def test_normalizes_opencli_tweet(self):
        record = {
            "id": "12345",
            "author": "sama",
            "text": "OpenCLI now powers this digest.",
            "created_at": "Fri May 08 04:03:02 +0000 2026",
            "likes": 10,
            "retweets": 2,
            "replies": 3,
            "views": 400,
            "url": "https://x.com/sama/status/12345",
            "is_retweet": False,
        }

        article = fetch_twitter.normalize_opencli_tweet(record, self.source, self.cutoff)

        self.assertEqual(article["title"], "OpenCLI now powers this digest.")
        self.assertEqual(article["link"], "https://x.com/sama/status/12345")
        self.assertEqual(article["date"], "2026-05-08T04:03:02+00:00")
        self.assertEqual(article["topics"], ["llm", "ai-agent"])
        self.assertEqual(article["tweet_id"], "12345")
        self.assertEqual(
            article["metrics"],
            {
                "like_count": 10,
                "retweet_count": 2,
                "reply_count": 3,
                "quote_count": 0,
                "impression_count": 400,
            },
        )

    def test_builds_link_and_defaults_metrics(self):
        record = {
            "id": "67890",
            "author": "sama",
            "text": "No metrics here.",
            "created_at": "2026-05-08T05:00:00Z",
            "is_retweet": False,
        }

        article = fetch_twitter.normalize_opencli_tweet(record, self.source, self.cutoff)

        self.assertEqual(article["link"], "https://x.com/sama/status/67890")
        self.assertEqual(
            article["metrics"],
            {
                "like_count": 0,
                "retweet_count": 0,
                "reply_count": 0,
                "quote_count": 0,
                "impression_count": 0,
            },
        )

    def test_skips_old_tweets(self):
        record = {
            "id": "old",
            "author": "sama",
            "text": "Old news.",
            "created_at": "2026-05-07T23:59:59Z",
            "is_retweet": False,
        }

        self.assertIsNone(fetch_twitter.normalize_opencli_tweet(record, self.source, self.cutoff))

    def test_skips_retweets(self):
        records = [
            {
                "id": "rt1",
                "author": "sama",
                "text": "A retweet.",
                "created_at": "2026-05-08T05:00:00Z",
                "is_retweet": True,
            },
            {
                "id": "rt2",
                "author": "sama",
                "text": "RT @openai: A retweet.",
                "created_at": "2026-05-08T05:00:00Z",
                "is_retweet": False,
            },
        ]

        for record in records:
            with self.subTest(record=record["id"]):
                self.assertIsNone(fetch_twitter.normalize_opencli_tweet(record, self.source, self.cutoff))

    def test_skips_malformed_records(self):
        records = [
            {"author": "sama", "text": "Missing ID", "created_at": "2026-05-08T05:00:00Z"},
            {"id": "1", "author": "sama", "created_at": "2026-05-08T05:00:00Z"},
            {"id": "1", "author": "sama", "text": "Missing date"},
            {"id": "1", "author": "sama", "text": "Bad date", "created_at": "not a date"},
        ]

        for record in records:
            with self.subTest(record=record):
                self.assertIsNone(fetch_twitter.normalize_opencli_tweet(record, self.source, self.cutoff))

    def test_extracts_records_from_common_json_shapes(self):
        shapes = [
            [{"id": "1"}],
            {"tweets": [{"id": "1"}]},
            {"data": [{"id": "1"}]},
            {"data": {"tweets": [{"id": "1"}]}},
            {"items": [{"id": "1"}]},
        ]

        for payload in shapes:
            with self.subTest(payload=payload):
                self.assertEqual(fetch_twitter.extract_opencli_tweet_records(payload), [{"id": "1"}])


class TestOpenCliDiscovery(unittest.TestCase):
    def test_resolves_opencli_bin_from_env(self):
        with patch.dict(os.environ, {"OPENCLI_BIN": "/custom/opencli"}):
            self.assertEqual(fetch_twitter.resolve_opencli_bin(), "/custom/opencli")

    def test_resolves_opencli_bin_from_path(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "fetch_twitter.shutil.which",
            return_value="/usr/local/bin/opencli",
        ):
            self.assertEqual(fetch_twitter.resolve_opencli_bin(), "/usr/local/bin/opencli")

    def test_missing_opencli_bin_raises(self):
        with patch.dict(os.environ, {}, clear=True), patch("fetch_twitter.shutil.which", return_value=None):
            with self.assertRaises(fetch_twitter.OpenCliBackendError) as ctx:
                fetch_twitter.resolve_opencli_bin()
        self.assertEqual(ctx.exception.code, "opencli_missing")

    def test_detects_twitter_tweets_capability_from_list_json(self):
        payloads = [
            [{"site": "twitter", "name": "tweets"}],
            {"commands": [{"site": "twitter", "name": "tweets"}]},
            {"commands": [{"command": "twitter tweets"}]},
            {"twitter": ["tweets", "timeline"]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertTrue(fetch_twitter.opencli_has_twitter_tweets(payload))

    def test_rejects_missing_twitter_tweets_capability(self):
        payload = {"commands": [{"site": "twitter", "name": "search"}]}
        self.assertFalse(fetch_twitter.opencli_has_twitter_tweets(payload))


class TestBackendSelection(unittest.TestCase):
    def test_auto_backend_order_starts_with_opencli(self):
        self.assertEqual(
            fetch_twitter.get_backend_order("auto"),
            ["opencli", "getxapi", "twitterapiio", "official"],
        )

    def test_explicit_opencli_has_no_api_fallback(self):
        self.assertEqual(fetch_twitter.get_backend_order("opencli"), ["opencli"])

    def test_explicit_api_backend_order_is_single_backend(self):
        self.assertEqual(fetch_twitter.get_backend_order("getxapi"), ["getxapi"])
        self.assertEqual(fetch_twitter.get_backend_order("twitterapiio"), ["twitterapiio"])
        self.assertEqual(fetch_twitter.get_backend_order("official"), ["official"])

    def test_opencli_defaults_to_parallel_fetching(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(fetch_twitter.get_opencli_max_workers(), 10)

    def test_opencli_worker_count_can_be_configured(self):
        with patch.dict(os.environ, {"OPENCLI_MAX_WORKERS": "3"}, clear=True):
            self.assertEqual(fetch_twitter.get_opencli_max_workers(), 3)

    def test_opencli_worker_count_rejects_invalid_values(self):
        invalid_values = ["0", "-1", "abc"]
        for value in invalid_values:
            with self.subTest(value=value), patch.dict(os.environ, {"OPENCLI_MAX_WORKERS": value}, clear=True):
                with self.assertLogs(level="WARNING"):
                    self.assertEqual(fetch_twitter.get_opencli_max_workers(), 10)

    def test_opencli_worker_count_caps_to_max(self):
        with patch.dict(os.environ, {"OPENCLI_MAX_WORKERS": "20"}, clear=True):
            with self.assertLogs(level="WARNING"):
                self.assertEqual(fetch_twitter.get_opencli_max_workers(), 10)

    def test_parses_opencli_update_command_override(self):
        with patch.dict(os.environ, {"OPENCLI_UPDATE_COMMAND": "self-update --yes"}, clear=True):
            self.assertEqual(
                fetch_twitter._parse_opencli_update_command_spec(),
                [["self-update", "--yes"]],
            )


class TestOpenCliAutoUpdate(unittest.TestCase):
    @patch("fetch_twitter._run_opencli_update_command")
    @patch("fetch_twitter._record_opencli_update_state")
    @patch("fetch_twitter._opencli_update_state_path", return_value=Path("/tmp/opencli-update-state.json"))
    @patch("fetch_twitter._is_opencli_update_due", return_value=True)
    @patch.dict(os.environ, {"OPENCLI_UPDATE_COMMAND": "self-update --yes"}, clear=True)
    def test_ensure_opencli_latest_uses_custom_update_command(
        self,
        is_due_mock,
        state_path_mock,
        record_state_mock,
        run_mock,
    ):
        run_mock.return_value = subprocess.CompletedProcess(
            args=["/bin/opencli", "self-update", "--yes"],
            returncode=0,
            stdout="updated",
            stderr="",
        )

        result = fetch_twitter._ensure_opencli_latest("/bin/opencli")

        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["command"], "/bin/opencli self-update --yes")
        run_mock.assert_called_once_with("/bin/opencli", ["self-update", "--yes"])

    @patch("fetch_twitter._run_opencli_update_command")
    @patch.dict(os.environ, {"OPENCLI_NO_UPDATE": "1"}, clear=True)
    def test_ensure_opencli_latest_skips_when_no_update_enabled(
        self,
        run_mock,
    ):
        result = fetch_twitter._ensure_opencli_latest("/bin/opencli")

        self.assertEqual(result["status"], "skipped")
        self.assertIn("OpenCLI auto-update is disabled", result["message"])
        run_mock.assert_not_called()


class TestOpenCliSelectionDiagnostics(unittest.TestCase):
    def test_empty_reason_prioritizes_opencli_failure(self):
        diagnostics = [
            {
                "backend": "opencli",
                "code": "opencli_browser_unavailable",
                "message": "No tab with id: 123",
            },
            {
                "backend": "twitterapiio",
                "code": "backend_unavailable",
                "message": "twitterapiio backend is not configured",
            },
            {
                "backend": "official",
                "code": "backend_unavailable",
                "message": "official backend is not configured",
            },
        ]

        reason = fetch_twitter.build_twitter_skipped_reason("auto", diagnostics)

        self.assertIn("OpenCLI failed first", reason)
        self.assertIn("opencli_browser_unavailable", reason)
        self.assertIn("No tab with id", reason)
        self.assertIn("twitterapi.io", reason)
        self.assertIn("X_BEARER_TOKEN", reason)

    def test_stale_browser_bridge_errors_are_global_opencli_failures(self):
        stale_errors = [
            "No tab with id: 123",
            "SecurityError: Failed to execute 'pushState' on 'History'",
        ]

        for message in stale_errors:
            with self.subTest(message=message):
                code = fetch_twitter._classify_opencli_failure(1, message)

                self.assertEqual(code, "opencli_browser_unavailable")

    def test_stale_browser_bridge_errors_in_stdout_are_global_opencli_failures(self):
        code = fetch_twitter._classify_opencli_failure(1, stdout="No tab with id: 123")
        self.assertEqual(code, "opencli_browser_unavailable")

class TestOpenCliBackend(unittest.TestCase):
    def setUp(self):
        self._env_patch = patch.dict(os.environ, {"OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN": "0"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        self.source = {
            "id": "sama-twitter",
            "type": "twitter",
            "name": "Sam Altman",
            "handle": "sama",
            "enabled": True,
            "priority": True,
            "topics": ["llm"],
        }
        self.cutoff = utc("2026-05-08T00:00:00Z")

    def _completed(self, stdout, returncode=0, stderr=""):
        return subprocess.CompletedProcess(
            args=["opencli"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_fetches_tweets_for_source(self, run_mock, _resolve_mock):
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
            self._completed(json.dumps({"tabs": []})),
            self._completed(json.dumps([
                {
                    "id": "123",
                    "author": "sama",
                    "text": "A useful post.",
                    "created_at": "2026-05-08T01:00:00Z",
                    "likes": 5,
                    "retweets": 1,
                    "replies": 0,
                    "views": 100,
                    "url": "https://x.com/sama/status/123",
                    "is_retweet": False,
                }
            ])),
            self._completed(json.dumps({"tabs": []})),
            self._completed("lease released"),
        ]

        backend = fetch_twitter.OpenCliBackend()
        results = backend.fetch_all([self.source], self.cutoff)

        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["handle"], "sama")
        self.assertEqual(results[0]["count"], 1)
        self.assertEqual(results[0]["articles"][0]["tweet_id"], "123")
        self.assertIn(
            [
                "/bin/opencli",
                "twitter",
                "tweets",
                "sama",
                "--limit",
                "20",
                "-f",
                "json",
            ],
            [call.args[0] for call in run_mock.call_args_list],
        )

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_closes_new_twitter_tabs_after_fetch(self, run_mock, _resolve_mock):
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
            self._completed(json.dumps({
                "tabs": [
                    {"targetId": "existing-x-tab", "url": "https://x.com/home"},
                ],
            })),
            self._completed(json.dumps([
                {
                    "id": "123",
                    "author": "sama",
                    "text": "A useful post.",
                    "created_at": "2026-05-08T01:00:00Z",
                    "url": "https://x.com/sama/status/123",
                    "is_retweet": False,
                }
            ])),
            self._completed(json.dumps({
                "tabs": [
                    {"targetId": "existing-x-tab", "url": "https://x.com/home"},
                    {"targetId": "new-x-tab", "url": "https://x.com/sama/status/123"},
                    {"page": "new-doc-tab", "url": "https://example.com/docs"},
                    {"page": "new-twitter-tab", "url": "https://twitter.com/sama/status/456"},
                ],
            })),
            self._completed("closed"),
            self._completed("closed"),
            self._completed("lease released"),
        ]

        backend = fetch_twitter.OpenCliBackend()
        backend.fetch_all([self.source], self.cutoff)

        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(["/bin/opencli", "browser", "tab", "close", "new-x-tab"], commands)
        self.assertIn(["/bin/opencli", "browser", "tab", "close", "new-twitter-tab"], commands)
        self.assertIn(["/bin/opencli", "browser", "close"], commands)
        self.assertNotIn(["/bin/opencli", "browser", "tab", "close", "existing-x-tab"], commands)
        self.assertNotIn(["/bin/opencli", "browser", "tab", "close", "new-doc-tab"], commands)

    @patch("fetch_twitter.time.sleep")
    @patch("fetch_twitter.OpenCliBackend._run_command")
    def test_closes_new_opencli_twitter_tabs_with_single_pass(self, run_command_mock, sleep_mock):
        backend = fetch_twitter.OpenCliBackend.__new__(fetch_twitter.OpenCliBackend)
        backend.command = "/bin/opencli"

        run_command_mock.side_effect = [
            self._completed(
                json.dumps({
                    "tabs": [
                        {"targetId": "existing-x-tab", "url": "https://x.com/home"},
                        {"targetId": "new-twitter-tab", "url": "https://twitter.com/sama/status/456"},
                    ]
                })
            ),
            self._completed("closed"),
        ]

        backend._cleanup_new_browser_tabs({"existing-x-tab": "https://x.com/home"})

        calls = [call.args[0] for call in run_command_mock.call_args_list]
        self.assertEqual(calls[0], ["browser", "tab", "list"])
        self.assertEqual(calls[1:], [["browser", "tab", "close", "new-twitter-tab"]])
        self.assertEqual(sleep_mock.call_count, 1)
        self.assertEqual(len(calls), 2)

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_missing_capability_raises_global_error(self, run_mock, _resolve_mock):
        run_mock.return_value = self._completed("", returncode=1, stderr="Unknown command: tweets")

        with self.assertRaises(fetch_twitter.OpenCliBackendError) as ctx:
            fetch_twitter.OpenCliBackend()

        self.assertEqual(ctx.exception.code, "opencli_capability_missing")

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_auth_required_raises_global_error_on_probe(self, run_mock, _resolve_mock):
        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
            self._completed(json.dumps({"tabs": []})),
            self._completed("", returncode=77, stderr="Not logged into x.com"),
            self._completed(json.dumps({"tabs": []})),
            self._completed("lease released"),
        ]

        backend = fetch_twitter.OpenCliBackend()

        with self.assertRaises(fetch_twitter.OpenCliBackendError) as ctx:
            backend.fetch_all([self.source], self.cutoff)

        self.assertEqual(ctx.exception.code, "opencli_auth_required")

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_single_source_error_does_not_raise_global_error(self, run_mock, _resolve_mock):
        second_source = dict(self.source)
        second_source["id"] = "openai-twitter"
        second_source["name"] = "OpenAI"
        second_source["handle"] = "OpenAI"

        run_mock.side_effect = [
            self._completed("Usage: opencli twitter tweets <username> [options]"),
            self._completed("OpenCLI doctor ok"),
            self._completed(json.dumps({"tabs": []})),
            self._completed(json.dumps([
                {
                    "id": "probe",
                    "author": "sama",
                    "text": "Probe works.",
                    "created_at": "2026-05-08T01:00:00Z",
                    "is_retweet": False,
                }
            ])),
            self._completed("", returncode=1, stderr="Could not resolve @OpenAI"),
            self._completed(json.dumps({"tabs": []})),
            self._completed("lease released"),
        ]

        backend = fetch_twitter.OpenCliBackend()
        with self.assertLogs(level="WARNING") as logs:
            results = backend.fetch_all([self.source, second_source], self.cutoff)

        by_handle = {item["handle"]: item for item in results}
        self.assertEqual(by_handle["sama"]["status"], "ok")
        self.assertEqual(by_handle["OpenAI"]["status"], "error")
        self.assertIn("opencli_source_error", by_handle["OpenAI"]["error"])
        self.assertTrue(any("opencli_source_error" in line for line in logs.output))


class TestFetchWithBackendChain(unittest.TestCase):
    @patch("fetch_twitter.OpenCliBackend")
    def test_fetch_with_backend_chain_passes_opencli_workers(self, backend_cls_mock):
        source = {
            "id": "sama-twitter",
            "type": "twitter",
            "name": "Sam Altman",
            "handle": "sama",
            "enabled": True,
            "priority": True,
            "topics": ["llm"],
        }
        backend = backend_cls_mock.return_value
        backend.fetch_all.return_value = []
        backend_cls_mock.return_value = backend

        fetch_twitter.fetch_with_backend_chain(
            "opencli",
            [source],
            utc("2026-05-08T00:00:00Z"),
            opencli_workers=7,
        )

        backend_cls_mock.assert_called_once_with(max_workers=7)


class TestMainOpenCliOptions(unittest.TestCase):
    @patch("fetch_twitter.open", new_callable=mock_open)
    @patch("fetch_twitter._opencli_auto_update_enabled", return_value=True)
    @patch("fetch_twitter.fetch_with_backend_chain")
    @patch("fetch_twitter.load_twitter_sources")
    def test_no_update_flag_disables_opencli_auto_update(
        self,
        load_sources_mock,
        backend_chain_mock,
        opencli_auto_update_mock,
        _open_mock,
    ):
        load_sources_mock.return_value = []
        backend_chain_mock.return_value = ("opencli", [], [])

        with patch.object(sys, "argv", ["fetch-twitter.py", "--backend", "opencli", "--no-update"]):
            self.assertEqual(fetch_twitter.main(), 0)

        self.assertEqual(backend_chain_mock.call_args[1]["opencli_auto_update"], False)
        self.assertEqual(opencli_auto_update_mock.call_count, 1)

    @patch("fetch_twitter.open", new_callable=mock_open)
    @patch("fetch_twitter._opencli_auto_update_enabled", return_value=True)
    @patch("fetch_twitter.fetch_with_backend_chain")
    @patch("fetch_twitter.load_twitter_sources")
    def test_no_update_flag_controls_update_toggle(
        self,
        load_sources_mock,
        backend_chain_mock,
        opencli_auto_update_mock,
        _open_mock,
    ):
        load_sources_mock.return_value = []
        backend_chain_mock.return_value = ("opencli", [], [])

        with patch.object(sys, "argv", ["fetch-twitter.py", "--backend", "opencli"]):
            self.assertEqual(fetch_twitter.main(), 0)
        self.assertEqual(backend_chain_mock.call_args[1]["opencli_auto_update"], True)

        with patch.object(sys, "argv", ["fetch-twitter.py", "--backend", "opencli", "--no-update"]):
            self.assertEqual(fetch_twitter.main(), 0)
        self.assertEqual(backend_chain_mock.call_args_list[1][1]["opencli_auto_update"], False)
        self.assertEqual(opencli_auto_update_mock.call_count, 2)

    @patch("fetch_twitter.open", new_callable=mock_open)
    @patch("fetch_twitter.fetch_with_backend_chain")
    @patch("fetch_twitter.load_twitter_sources")
    def test_opencli_no_update_env_prevents_auto_update(
        self,
        load_sources_mock,
        backend_chain_mock,
        _open_mock,
    ):
        load_sources_mock.return_value = []
        backend_chain_mock.return_value = ("opencli", [], [])

        with patch.dict(os.environ, {"OPENCLI_NO_UPDATE": "1", "OPENCLI_AUTO_UPDATE": "1"}, clear=True):
            with patch.object(sys, "argv", ["fetch-twitter.py", "--backend", "opencli"]):
                self.assertEqual(fetch_twitter.main(), 0)
            self.assertEqual(backend_chain_mock.call_args_list[0][1]["opencli_auto_update"], False)

            backend_chain_mock.reset_mock()
            with patch.object(sys, "argv", ["fetch-twitter.py", "--backend", "opencli", "--no-update"]):
                self.assertEqual(fetch_twitter.main(), 0)
            self.assertEqual(backend_chain_mock.call_args_list[0][1]["opencli_auto_update"], False)




class TestOpenCliChromeCleanup(unittest.TestCase):
    def test_parses_chrome_window_snapshot(self):
        raw = (
            "101\thttps://github.com/tangwz/follow-news ||| https://x.com/home\n"
            "202\tabout:blank ||| chrome://newtab/\n"
            "303tabhttps://x.com/explore\n"
        )

        windows = fetch_twitter.parse_chrome_window_snapshot(raw)

        self.assertEqual(
            windows,
            {
                "101": ["https://github.com/tangwz/follow-news", "https://x.com/home"],
                "202": ["about:blank", "chrome://newtab/"],
                "303": ["https://x.com/explore"],
            },
        )

    def test_identifies_opencli_chrome_windows(self):
        self.assertTrue(fetch_twitter.is_opencli_chrome_window(["about:blank"]))
        self.assertTrue(fetch_twitter.is_opencli_chrome_window(["chrome://newtab/", "https://x.com/explore"]))
        self.assertTrue(fetch_twitter.is_opencli_chrome_window(["https://twitter.com/sama/status/123"]))
        self.assertFalse(fetch_twitter.is_opencli_chrome_window(["https://github.com/tangwz/follow-news"]))
        self.assertFalse(fetch_twitter.is_opencli_chrome_window(["https://x.com/home", "https://github.com/"]))

    @patch("fetch_twitter.time.sleep")
    @patch("fetch_twitter.close_chrome_windows")
    @patch("fetch_twitter.snapshot_chrome_windows")
    def test_closes_only_new_opencli_chrome_windows(self, snapshot_mock, close_mock, _sleep_mock):
        before = {
            "1": ["https://github.com/tangwz/follow-news"],
            "2": ["about:blank"],
        }
        snapshot_mock.side_effect = [
            {
                "1": ["https://github.com/tangwz/follow-news"],
                "2": ["about:blank"],
                "3": ["about:blank", "https://x.com/explore"],
                "4": ["https://github.com/new"],
            },
            {
                "1": ["https://github.com/tangwz/follow-news"],
                "2": ["about:blank"],
                "4": ["https://github.com/new"],
            },
        ]

        fetch_twitter.cleanup_new_opencli_chrome_windows(before)

        close_mock.assert_called_once_with(["3"])

    @patch("fetch_twitter.time.sleep")
    @patch("fetch_twitter.close_chrome_windows")
    @patch("fetch_twitter.snapshot_chrome_windows")
    def test_retries_delayed_opencli_chrome_window_cleanup(self, snapshot_mock, close_mock, sleep_mock):
        before = {
            "1": ["https://github.com/tangwz/follow-news"],
        }
        snapshot_mock.side_effect = [
            before,
            {
                "1": ["https://github.com/tangwz/follow-news"],
                "2": ["about:blank"],
            },
            before,
        ]

        fetch_twitter.cleanup_new_opencli_chrome_windows(before)

        sleep_mock.assert_any_call(0.5)
        close_mock.assert_called_once_with(["2"])


class TestBackendChain(unittest.TestCase):
    def setUp(self):
        self.sources = [
            {
                "id": "sama-twitter",
                "type": "twitter",
                "name": "Sam Altman",
                "handle": "sama",
                "enabled": True,
                "priority": True,
                "topics": ["llm"],
            }
        ]
        self.cutoff = utc("2026-05-08T00:00:00Z")

    @patch.dict(os.environ, {"GETX_API_KEY": "x" * 20}, clear=True)
    @patch("fetch_twitter.OpenCliBackend", side_effect=fetch_twitter.OpenCliBackendError("opencli_missing", "missing"))
    @patch("fetch_twitter.GetXApiBackend")
    def test_auto_falls_back_from_opencli_to_getxapi(self, getx_cls, _opencli_cls):
        getx = getx_cls.return_value
        getx.fetch_all.return_value = [{"status": "ok", "count": 0, "articles": []}]

        with self.assertLogs(level="WARNING") as logs:
            backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
                "auto", self.sources, self.cutoff, no_cache=False
            )

        self.assertEqual(backend_name, "getxapi")
        self.assertEqual(results, [{"status": "ok", "count": 0, "articles": []}])
        self.assertEqual(diagnostics[0]["backend"], "opencli")
        self.assertEqual(diagnostics[0]["code"], "opencli_missing")
        self.assertTrue(any("opencli_missing" in line for line in logs.output))

    @patch.dict(os.environ, {"GETX_API_KEY": "x" * 20}, clear=True)
    @patch("fetch_twitter.GetXApiBackend")
    @patch("fetch_twitter.OpenCliBackend")
    def test_auto_opencli_stale_bridge_failure_retried_once_before_fallback(self, opencli_cls, getx_cls):
        first_opencli_backend = MagicMock()
        second_opencli_backend = MagicMock()
        opencli_cls.side_effect = [first_opencli_backend, second_opencli_backend]
        first_opencli_backend.fetch_all.side_effect = fetch_twitter.OpenCliBackendError(
            "opencli_browser_unavailable",
            "No tab with id: 123",
        )
        second_opencli_backend.fetch_all.side_effect = fetch_twitter.OpenCliBackendError(
            "opencli_browser_unavailable",
            "SecurityError: Failed to execute 'pushState' on 'History'",
        )

        getx = getx_cls.return_value
        getx.fetch_all.return_value = [{"status": "ok", "count": 0, "articles": []}]

        with self.assertLogs(level="WARNING") as logs:
            backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
                "auto", self.sources, self.cutoff, no_cache=False
            )

        self.assertEqual(backend_name, "getxapi")
        self.assertEqual(results, [{"status": "ok", "count": 0, "articles": []}])
        self.assertEqual(opencli_cls.call_count, 2)
        self.assertEqual(getx_cls.call_count, 1)
        self.assertEqual(
            [item for item in diagnostics if item["backend"] == "opencli"],
            [
                {
                    "backend": "opencli",
                    "code": "opencli_browser_unavailable",
                    "message": "No tab with id: 123",
                },
                {
                    "backend": "opencli",
                    "code": "opencli_browser_unavailable",
                    "message": "SecurityError: Failed to execute 'pushState' on 'History'",
                },
            ],
        )
        self.assertTrue(any("opencli recovery attempt" in line for line in logs.output))

    @patch("fetch_twitter.OpenCliBackend")
    def test_explicit_opencli_retries_once_and_still_fails(self, opencli_cls):
        first_opencli_backend = MagicMock()
        second_opencli_backend = MagicMock()
        opencli_cls.side_effect = [first_opencli_backend, second_opencli_backend]
        first_opencli_backend.fetch_all.side_effect = fetch_twitter.OpenCliBackendError(
            "opencli_browser_unavailable",
            "No tab with id: 123",
        )
        second_opencli_backend.fetch_all.side_effect = fetch_twitter.OpenCliBackendError(
            "opencli_browser_unavailable",
            "SecurityError: Failed to execute 'pushState' on 'History'",
        )

        backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
            "opencli", self.sources, self.cutoff, no_cache=False
        )

        self.assertEqual(backend_name, "opencli")
        self.assertEqual(results, [])
        self.assertEqual(opencli_cls.call_count, 2)
        self.assertEqual(len([item for item in diagnostics if item["backend"] == "opencli"]), 2)
        self.assertEqual(diagnostics[0]["code"], "opencli_browser_unavailable")

    @patch.dict(os.environ, {}, clear=True)
    @patch("fetch_twitter.OpenCliBackend", side_effect=fetch_twitter.OpenCliBackendError("opencli_missing", "missing"))
    def test_explicit_opencli_does_not_fallback(self, _opencli_cls):
        with self.assertLogs(level="WARNING") as logs:
            backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
                "opencli", self.sources, self.cutoff, no_cache=False
            )

        self.assertEqual(backend_name, "opencli")
        self.assertEqual(results, [])
        self.assertEqual(diagnostics[0]["code"], "opencli_missing")
        self.assertTrue(any("opencli_missing" in line for line in logs.output))

    @patch.dict(os.environ, {}, clear=True)
    @patch("fetch_twitter.OpenCliBackend", side_effect=fetch_twitter.OpenCliBackendError("opencli_missing", "missing"))
    def test_auto_returns_no_results_when_no_backend_available(self, _opencli_cls):
        with self.assertLogs(level="WARNING") as logs:
            backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
                "auto", self.sources, self.cutoff, no_cache=False
            )

        self.assertEqual(backend_name, "auto")
        self.assertEqual(results, [])
        self.assertTrue(any(item["backend"] == "opencli" for item in diagnostics))
        self.assertTrue(any("opencli_missing" in line for line in logs.output))


class TestOpenCliMergeCompatibility(unittest.TestCase):
    def test_opencli_articles_keep_existing_merge_shape(self):
        merge_spec = importlib.util.spec_from_file_location(
            "merge_sources",
            SCRIPTS_DIR / "merge-sources.py",
        )
        merge_mod = importlib.util.module_from_spec(merge_spec)
        merge_spec.loader.exec_module(merge_mod)

        source = {
            "source_id": "sama-twitter",
            "source_type": "twitter",
            "name": "Sam Altman",
            "handle": "sama",
            "priority": True,
            "topics": ["llm"],
            "status": "ok",
            "attempts": 1,
            "count": 1,
            "articles": [],
        }
        config_source = {
            "id": "sama-twitter",
            "type": "twitter",
            "name": "Sam Altman",
            "handle": "sama",
            "enabled": True,
            "priority": True,
            "topics": ["llm"],
        }
        article = fetch_twitter.normalize_opencli_tweet(
            {
                "id": "123",
                "author": "sama",
                "text": "A high-signal tweet.",
                "created_at": "2026-05-08T05:00:00Z",
                "likes": 1000,
                "retweets": 20,
                "replies": 3,
                "views": 5000,
                "is_retweet": False,
            },
            config_source,
            utc("2026-05-08T00:00:00Z"),
        )
        article["source_type"] = "twitter"

        score = merge_mod.calculate_base_score(article, source)

        self.assertIn("title", article)
        self.assertIn("link", article)
        self.assertIn("date", article)
        self.assertIn("metrics", article)
        self.assertGreaterEqual(score, 8)
