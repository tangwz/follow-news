#!/usr/bin/env python3
"""Tests for fetch-podcast.py."""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("fetch_podcast", SCRIPTS_DIR / "fetch-podcast.py")
fetch_podcast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_podcast)


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TestPodcastPlatformInference(unittest.TestCase):
    def test_infers_youtube_platform(self):
        self.assertEqual(
            fetch_podcast.infer_platform("https://www.youtube.com/playlist?list=abc"),
            "youtube",
        )
        self.assertEqual(
            fetch_podcast.infer_platform("https://youtu.be/videoid"),
            "youtube",
        )

    def test_infers_rss_platform_for_non_youtube_url(self):
        self.assertEqual(
            fetch_podcast.infer_platform("https://example.com/feed.xml"),
            "rss",
        )


class TestPodcastDateParsing(unittest.TestCase):
    def test_parse_podcast_date_iso(self):
        parsed = fetch_podcast.parse_podcast_date("2026-05-04T20:05:00Z")
        self.assertEqual(parsed.isoformat(), "2026-05-04T20:05:00+00:00")

    def test_parse_podcast_date_rfc2822(self):
        parsed = fetch_podcast.parse_podcast_date("Mon, 04 May 2026 20:05:00 +0000")
        self.assertEqual(parsed.isoformat(), "2026-05-04T20:05:00+00:00")


class TestPodcastRssParsing(unittest.TestCase):
    def test_parse_rss_episodes(self):
        rss = """<?xml version="1.0"?>
<rss><channel>
<item>
  <title>Episode One</title>
  <guid>episode-one</guid>
  <link>https://example.com/episodes/1</link>
  <pubDate>Mon, 04 May 2026 20:05:00 +0000</pubDate>
</item>
<item>
  <title>Old Episode</title>
  <guid>old-episode</guid>
  <link>https://example.com/episodes/old</link>
  <pubDate>Mon, 01 Jan 2024 20:05:00 +0000</pubDate>
</item>
</channel></rss>"""
        source = {
            "id": "test-podcast",
            "name": "Test Podcast",
            "topics": ["llm"],
            "url": "https://example.com/feed.xml",
        }
        cutoff = utc("2026-05-01T00:00:00Z")

        episodes = fetch_podcast.parse_rss_episodes(rss, source, cutoff)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "Episode One")
        self.assertEqual(episodes[0]["guid"], "episode-one")
        self.assertEqual(episodes[0]["link"], "https://example.com/episodes/1")
        self.assertEqual(episodes[0]["topics"], ["llm"])
        self.assertEqual(episodes[0]["show_name"], "Test Podcast")
        self.assertEqual(episodes[0]["platform"], "rss")
        self.assertEqual(episodes[0]["transcript_status"], "disabled")


class TestPodcastSourceLoading(unittest.TestCase):
    def test_load_podcast_sources_filters_enabled_podcast_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            defaults = Path(tmpdir) / "defaults"
            defaults.mkdir()
            sources_path = defaults / "sources.json"
            sources_path.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "rss-source",
                                "type": "rss",
                                "name": "RSS",
                                "enabled": True,
                                "priority": False,
                                "url": "https://example.com/rss.xml",
                                "topics": ["llm"],
                            },
                            {
                                "id": "podcast-source",
                                "type": "podcast",
                                "name": "Podcast",
                                "enabled": True,
                                "priority": False,
                                "url": "https://example.com/feed.xml",
                                "topics": ["llm"],
                            },
                            {
                                "id": "disabled-podcast",
                                "type": "podcast",
                                "name": "Disabled",
                                "enabled": False,
                                "priority": False,
                                "url": "https://example.com/disabled.xml",
                                "topics": ["llm"],
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            sources = fetch_podcast.load_podcast_sources(defaults, None)

            self.assertEqual([s["id"] for s in sources], ["podcast-source"])
