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
