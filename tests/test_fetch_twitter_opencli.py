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
from unittest.mock import patch

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
            {"twitter": ["search", "timeline", "tweets"]},
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


class TestOpenCliBackend(unittest.TestCase):
    def setUp(self):
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
            self._completed(json.dumps({"commands": [{"site": "twitter", "name": "tweets"}]})),
            self._completed("OpenCLI doctor ok"),
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
        ]

        backend = fetch_twitter.OpenCliBackend()
        results = backend.fetch_all([self.source], self.cutoff)

        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["handle"], "sama")
        self.assertEqual(results[0]["count"], 1)
        self.assertEqual(results[0]["articles"][0]["tweet_id"], "123")
        self.assertIn(
            ["/bin/opencli", "twitter", "tweets", "sama", "--limit", "20", "-f", "json"],
            [call.args[0] for call in run_mock.call_args_list],
        )

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_missing_capability_raises_global_error(self, run_mock, _resolve_mock):
        run_mock.return_value = self._completed(json.dumps({"commands": [{"site": "twitter", "name": "search"}]}))

        with self.assertRaises(fetch_twitter.OpenCliBackendError) as ctx:
            fetch_twitter.OpenCliBackend()

        self.assertEqual(ctx.exception.code, "opencli_capability_missing")

    @patch("fetch_twitter.resolve_opencli_bin", return_value="/bin/opencli")
    @patch("subprocess.run")
    def test_auth_required_raises_global_error_on_probe(self, run_mock, _resolve_mock):
        run_mock.side_effect = [
            self._completed(json.dumps({"commands": [{"site": "twitter", "name": "tweets"}]})),
            self._completed("OpenCLI doctor ok"),
            self._completed("", returncode=77, stderr="Not logged into x.com"),
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
            self._completed(json.dumps({"commands": [{"site": "twitter", "name": "tweets"}]})),
            self._completed("OpenCLI doctor ok"),
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
        ]

        backend = fetch_twitter.OpenCliBackend()
        with self.assertLogs(level="WARNING") as logs:
            results = backend.fetch_all([self.source, second_source], self.cutoff)

        by_handle = {item["handle"]: item for item in results}
        self.assertEqual(by_handle["sama"]["status"], "ok")
        self.assertEqual(by_handle["OpenAI"]["status"], "error")
        self.assertIn("opencli_source_error", by_handle["OpenAI"]["error"])
        self.assertTrue(any("opencli_source_error" in line for line in logs.output))


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

        backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
            "auto", self.sources, self.cutoff, no_cache=False
        )

        self.assertEqual(backend_name, "getxapi")
        self.assertEqual(results, [{"status": "ok", "count": 0, "articles": []}])
        self.assertEqual(diagnostics[0]["backend"], "opencli")
        self.assertEqual(diagnostics[0]["code"], "opencli_missing")

    @patch.dict(os.environ, {}, clear=True)
    @patch("fetch_twitter.OpenCliBackend", side_effect=fetch_twitter.OpenCliBackendError("opencli_missing", "missing"))
    def test_explicit_opencli_does_not_fallback(self, _opencli_cls):
        backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
            "opencli", self.sources, self.cutoff, no_cache=False
        )

        self.assertEqual(backend_name, "opencli")
        self.assertEqual(results, [])
        self.assertEqual(diagnostics[0]["code"], "opencli_missing")

    @patch.dict(os.environ, {}, clear=True)
    @patch("fetch_twitter.OpenCliBackend", side_effect=fetch_twitter.OpenCliBackendError("opencli_missing", "missing"))
    def test_auto_returns_no_results_when_no_backend_available(self, _opencli_cls):
        backend_name, results, diagnostics = fetch_twitter.fetch_with_backend_chain(
            "auto", self.sources, self.cutoff, no_cache=False
        )

        self.assertEqual(backend_name, "auto")
        self.assertEqual(results, [])
        self.assertTrue(any(item["backend"] == "opencli" for item in diagnostics))
