#!/usr/bin/env python3
"""Tests for fetch-podcast.py."""

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
spec = importlib.util.spec_from_file_location("fetch_podcast", SCRIPTS_DIR / "fetch-podcast.py")
fetch_podcast = importlib.util.module_from_spec(spec)
sys.modules["fetch_podcast"] = fetch_podcast
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


class TestYoutubeMetadataNormalization(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "training-data-podcast",
            "name": "Training Data",
            "url": "https://www.youtube.com/playlist?list=PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8",
            "topics": ["llm", "ai-agent"],
            "transcript": {"enabled": True, "backend": "auto"},
        }
        self.cutoff = utc("2026-05-01T00:00:00Z")

    def test_normalizes_youtube_entries_from_ytdlp(self):
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "Waymo Autonomy",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "timestamp": 1777925100,
                    "duration": 3600,
                },
                {
                    "id": "old123",
                    "title": "Old Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=old123",
                    "timestamp": 1700000000,
                },
            ]
        }

        episodes = fetch_podcast.normalize_youtube_metadata(payload, self.source, self.cutoff)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "Waymo Autonomy")
        self.assertEqual(episodes[0]["guid"], "youtube:abc123")
        self.assertEqual(episodes[0]["link"], "https://www.youtube.com/watch?v=abc123")
        self.assertEqual(episodes[0]["platform"], "youtube")
        self.assertEqual(episodes[0]["duration_seconds"], 3600)
        self.assertEqual(episodes[0]["transcript_status"], "missing")

    def test_reconstructs_link_for_flat_youtube_entry(self):
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "Flat Entry",
                    "url": "abc123",
                    "timestamp": 1777925100,
                },
            ]
        }

        episodes = fetch_podcast.normalize_youtube_metadata(payload, self.source, self.cutoff)

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["link"], "https://www.youtube.com/watch?v=abc123")


class TestTranscriptBackend(unittest.TestCase):
    def setUp(self):
        self.source = {
            "id": "training-data-podcast",
            "name": "Training Data",
            "url": "https://www.youtube.com/playlist?list=listid",
            "topics": ["llm"],
            "transcript": {
                "enabled": True,
                "backend": "auto",
                "languages": ["en", "zh"],
            },
        }
        self.episode = {
            "title": "Episode",
            "link": "https://www.youtube.com/watch?v=abc123",
            "date": "2026-05-04T20:05:00+00:00",
            "guid": "youtube:abc123",
            "topics": ["llm"],
            "show_name": "Training Data",
            "platform": "youtube",
            "transcript_status": "missing",
        }

    @patch("fetch_podcast.resolve_ytdlp_bin", return_value=None)
    def test_transcript_backend_unavailable_keeps_episode(self, _resolve):
        result = fetch_podcast.enrich_episode_transcript(self.episode.copy(), self.source, {}, no_cache=True)

        self.assertEqual(result["transcript_status"], "backend_unavailable")
        self.assertIn("transcript_error", result)
        self.assertNotIn("transcript", result)

    @patch("fetch_podcast.run_ytdlp_transcript")
    @patch("fetch_podcast.resolve_ytdlp_bin", return_value="/usr/local/bin/yt-dlp")
    def test_transcript_success_attaches_text(self, _resolve, run_transcript):
        run_transcript.return_value = {
            "status": "ok",
            "transcript": "Speaker 1 | 00:00 - 00:05 Hello world.",
        }

        result = fetch_podcast.enrich_episode_transcript(self.episode.copy(), self.source, {}, no_cache=True)

        self.assertEqual(result["transcript_status"], "ok")
        self.assertEqual(result["transcript"], "Speaker 1 | 00:00 - 00:05 Hello world.")

    def test_cache_key_uses_guid(self):
        key = fetch_podcast.transcript_cache_key(self.episode)

        self.assertEqual(key, "youtube:abc123")

    @patch("subprocess.run", side_effect=OSError("missing binary"))
    def test_ytdlp_os_error_returns_error_status(self, _run):
        result = fetch_podcast.run_ytdlp_transcript(
            "/missing/yt-dlp",
            self.episode,
            ["en"],
        )

        self.assertEqual(result["status"], "error")
        self.assertIn("missing binary", result["error"])

    def test_parse_vtt_skips_note_blocks_and_deduplicates_adjacent_text(self):
        content = """WEBVTT

NOTE
This note should not leak.
Another note line.

00:00:00.000 --> 00:00:05.000
<c>Hello world.</c>

00:00:05.000 --> 00:00:10.000
Hello world.

00:00:10.000 --> 00:00:15.000
Next line.
"""

        transcript = fetch_podcast.parse_vtt_transcript(content)

        self.assertNotIn("note should not leak", transcript)
        self.assertEqual(transcript.count("Hello world."), 1)
        self.assertIn("Next line.", transcript)

    def test_transcript_languages_falls_back_for_blank_config(self):
        source = {
            "transcript": {
                "enabled": True,
                "backend": "auto",
                "languages": ["", "  "],
            },
        }

        self.assertEqual(fetch_podcast.transcript_languages(source), ["en", "zh", "zh-Hans"])
