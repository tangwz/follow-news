#!/usr/bin/env python3
"""Tests for fetch-podcast.py."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
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

    def test_feedparser_path_scans_full_feed_before_limiting(self):
        old_entries = [
            {
                "title": f"Older Episode {index}",
                "id": f"older-{index}",
                "link": f"https://example.com/episodes/older-{index}",
                "published": f"Mon, {index + 1:02d} May 2026 20:05:00 +0000",
            }
            for index in range(fetch_podcast.MAX_EPISODES_PER_SOURCE)
        ]
        newest_entry = {
            "title": "Newest Episode",
            "id": "newest",
            "link": "https://example.com/episodes/newest",
            "published": "Sun, 31 May 2026 20:05:00 +0000",
        }

        class FakeFeedparser:
            @staticmethod
            def parse(_content):
                return type("Feed", (), {"entries": old_entries + [newest_entry]})()

        source = {
            "id": "test-podcast",
            "name": "Test Podcast",
            "topics": ["llm"],
            "url": "https://example.com/feed.xml",
        }
        cutoff = utc("2026-05-01T00:00:00Z")

        with patch.object(fetch_podcast, "HAS_FEEDPARSER", True):
            with patch.object(fetch_podcast, "feedparser", FakeFeedparser, create=True):
                episodes = fetch_podcast.parse_rss_episodes("", source, cutoff)

        self.assertEqual(len(episodes), fetch_podcast.MAX_EPISODES_PER_SOURCE)
        self.assertEqual(episodes[0]["guid"], "newest")
        self.assertNotIn("older-0", {episode["guid"] for episode in episodes})


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

    def test_normalizes_youtube_entries_newest_first_before_limit(self):
        payload = {
            "entries": [
                {
                    "id": "older",
                    "title": "Older Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=older",
                    "upload_date": "20260502",
                },
                {
                    "id": "newer",
                    "title": "Newer Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=newer",
                    "upload_date": "20260504",
                },
            ]
        }

        episodes = fetch_podcast.normalize_youtube_metadata(payload, self.source, self.cutoff)

        self.assertEqual(
            [episode["guid"] for episode in episodes],
            ["youtube:newer", "youtube:older"],
        )

    def test_normalizes_youtube_entries_deduplicates_playlist_edges(self):
        payload = {
            "entries": [
                {
                    "id": "same",
                    "title": "Same Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=same",
                    "upload_date": "20260504",
                },
                {
                    "id": "same",
                    "title": "Same Episode Duplicate",
                    "webpage_url": "https://www.youtube.com/watch?v=same",
                    "upload_date": "20260504",
                },
            ]
        }

        episodes = fetch_podcast.normalize_youtube_metadata(payload, self.source, self.cutoff)

        self.assertEqual([episode["guid"] for episode in episodes], ["youtube:same"])


    @patch("fetch_podcast.run_ytdlp_video_metadata")
    def test_hydrates_flat_youtube_entry_dates_before_normalization(self, run_video_metadata):
        run_video_metadata.return_value = {
            "id": "abc123",
            "title": "Hydrated Episode",
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "upload_date": "20260504",
            "duration": 1800,
        }
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "Flat Episode",
                    "url": "abc123",
                },
            ]
        }

        hydrated = fetch_podcast.hydrate_youtube_metadata(payload, "/usr/local/bin/yt-dlp")
        episodes = fetch_podcast.normalize_youtube_metadata(hydrated, self.source, self.cutoff)

        run_video_metadata.assert_called_once_with(
            "/usr/local/bin/yt-dlp",
            "https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "Hydrated Episode")
        self.assertEqual(episodes[0]["date"], "2026-05-04T00:00:00+00:00")
        self.assertEqual(episodes[0]["duration_seconds"], 1800)

    @patch("fetch_podcast.run_ytdlp_video_metadata")
    def test_hydrates_date_only_youtube_entry_before_hour_cutoff(self, run_video_metadata):
        run_video_metadata.return_value = {
            "id": "abc123",
            "title": "Hydrated Date Only Episode",
            "webpage_url": "https://www.youtube.com/watch?v=abc123",
            "timestamp": 1777925100,
        }
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "Date Only Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "upload_date": "20260504",
                },
            ]
        }

        hydrated = fetch_podcast.hydrate_youtube_metadata(payload, "/usr/local/bin/yt-dlp")
        episodes = fetch_podcast.normalize_youtube_metadata(
            hydrated,
            self.source,
            utc("2026-05-04T12:00:00Z"),
        )

        run_video_metadata.assert_called_once_with(
            "/usr/local/bin/yt-dlp",
            "https://www.youtube.com/watch?v=abc123",
        )
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["date"], "2026-05-04T20:05:00+00:00")

    @patch("fetch_podcast.run_ytdlp_video_metadata")
    def test_skips_hydration_for_precise_youtube_timestamp(self, run_video_metadata):
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "Timestamped Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "timestamp": 1777925100,
                },
            ]
        }

        hydrated = fetch_podcast.hydrate_youtube_metadata(payload, "/usr/local/bin/yt-dlp")

        run_video_metadata.assert_not_called()
        self.assertEqual(hydrated["entries"][0]["title"], "Timestamped Episode")

    @patch("fetch_podcast.run_ytdlp_video_metadata")
    def test_hydrates_tail_playlist_window_before_normalization(self, run_video_metadata):
        run_video_metadata.return_value = {
            "id": "tail",
            "title": "Tail Window Episode",
            "webpage_url": "https://www.youtube.com/watch?v=tail",
            "upload_date": "20260531",
        }
        entries = [
            {
                "id": f"head-{index}",
                "title": f"Head Episode {index}",
                "webpage_url": f"https://www.youtube.com/watch?v=head-{index}",
                "timestamp": 1777665900,
            }
            for index in range(fetch_podcast.MAX_EPISODES_PER_SOURCE)
        ]
        entries.append(
            {
                "id": "tail",
                "title": "Flat Tail Episode",
                "url": "tail",
            }
        )

        hydrated = fetch_podcast.hydrate_youtube_metadata(
            {"entries": entries},
            "/usr/local/bin/yt-dlp",
        )
        episodes = fetch_podcast.normalize_youtube_metadata(hydrated, self.source, self.cutoff)

        run_video_metadata.assert_called_once_with(
            "/usr/local/bin/yt-dlp",
            "https://www.youtube.com/watch?v=tail",
        )
        self.assertEqual(episodes[0]["guid"], "youtube:tail")


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

    @patch("subprocess.run")
    def test_ytdlp_transcript_uses_language_priority(self, run):
        def write_subtitle(cmd, **_kwargs):
            language = cmd[cmd.index("--sub-langs") + 1]
            output = Path(cmd[cmd.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            (output.parent / f"abc.{language}.vtt").write_text(
                f"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n{language} transcript.\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        run.side_effect = write_subtitle

        result = fetch_podcast.run_ytdlp_transcript(
            "/usr/local/bin/yt-dlp",
            self.episode,
            ["zh", "en"],
        )

        self.assertEqual(result["status"], "ok")
        self.assertIn("zh transcript.", result["transcript"])
        self.assertEqual(run.call_count, 1)
        self.assertIn("--no-playlist", run.call_args.args[0])

    @patch("subprocess.run")
    def test_ytdlp_transcript_falls_back_to_next_language(self, run):
        def maybe_write_subtitle(cmd, **_kwargs):
            language = cmd[cmd.index("--sub-langs") + 1]
            output = Path(cmd[cmd.index("--output") + 1])
            output.parent.mkdir(parents=True, exist_ok=True)
            if language == "en":
                (output.parent / "abc.en.vtt").write_text(
                    "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nEnglish transcript.\n",
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        run.side_effect = maybe_write_subtitle

        result = fetch_podcast.run_ytdlp_transcript(
            "/usr/local/bin/yt-dlp",
            self.episode,
            ["zh", "en"],
        )

        called_languages = [
            call.args[0][call.args[0].index("--sub-langs") + 1]
            for call in run.call_args_list
        ]
        self.assertEqual(called_languages, ["zh", "en"])
        self.assertEqual(result["status"], "ok")
        self.assertIn("English transcript.", result["transcript"])

    def test_cache_key_uses_guid(self):
        key = fetch_podcast.transcript_cache_key(self.episode, self.source)

        self.assertEqual(key, "training-data-podcast:youtube:abc123")

    def test_cache_key_namespaces_same_guid_by_source(self):
        other_source = {
            "id": "other-podcast",
            "name": "Other Podcast",
            "url": "https://example.com/feed.xml",
        }

        key = fetch_podcast.transcript_cache_key(self.episode, self.source)
        other_key = fetch_podcast.transcript_cache_key(self.episode, other_source)

        self.assertNotEqual(key, other_key)
        self.assertEqual(other_key, "other-podcast:youtube:abc123")

    @patch("fetch_podcast.resolve_ytdlp_bin", return_value=None)
    def test_transcript_cache_does_not_cross_sources_with_same_guid(self, _resolve):
        other_source = {
            **self.source,
            "id": "other-podcast",
            "name": "Other Podcast",
        }
        cache = {
            "transcripts": {
                fetch_podcast.transcript_cache_key(self.episode, self.source): {
                    "status": "ok",
                    "transcript": "Wrong source transcript.",
                    "error": "",
                    "ts": time.time(),
                }
            }
        }

        result = fetch_podcast.enrich_episode_transcript(
            self.episode.copy(),
            other_source,
            cache,
        )

        self.assertEqual(result["transcript_status"], "backend_unavailable")
        self.assertNotIn("transcript", result)

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


class TestPodcastCliOutput(unittest.TestCase):
    @patch("fetch_podcast.fetch_source")
    def test_run_fetch_writes_expected_output_shape(self, fetch_source):
        source = {
            "id": "test-podcast",
            "type": "podcast",
            "name": "Test Podcast",
            "enabled": True,
            "priority": True,
            "url": "https://example.com/feed.xml",
            "topics": ["llm"],
        }
        fetch_source.return_value = {
            "source_id": "test-podcast",
            "source_type": "podcast",
            "name": "Test Podcast",
            "url": "https://example.com/feed.xml",
            "priority": True,
            "topics": ["llm"],
            "status": "ok",
            "attempts": 1,
            "count": 1,
            "articles": [
                {
                    "title": "Episode",
                    "link": "https://example.com/episode",
                    "date": "2026-05-04T20:05:00+00:00",
                    "guid": "episode",
                    "topics": ["llm"],
                    "show_name": "Test Podcast",
                    "platform": "rss",
                    "transcript_status": "disabled",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "podcast.json"
            cache = {"transcripts": {}}
            result = fetch_podcast.run_fetch([source], 48, output, cache, no_cache=True)

            data = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(data["source_type"], "podcast")
        self.assertEqual(data["total_articles"], 1)
        self.assertEqual(data["sources"][0]["source_id"], "test-podcast")

    def test_save_podcast_cache_round_trips_via_atomic_path(self):
        cache = {
            "metadata": {},
            "transcripts": {
                "episode": {
                    "status": "ok",
                    "transcript": "Text",
                    "error": "",
                    "ts": 1777925100,
                }
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "podcast-cache.json"
            with patch.object(fetch_podcast, "PODCAST_CACHE_PATH", str(cache_path)):
                fetch_podcast.save_podcast_cache(cache)

                loaded = fetch_podcast.load_podcast_cache()

        self.assertEqual(loaded, cache)

    @patch("fetch_podcast.hydrate_youtube_metadata")
    @patch("fetch_podcast.run_ytdlp_metadata")
    @patch("fetch_podcast.resolve_ytdlp_bin", return_value="/usr/local/bin/yt-dlp")
    def test_fetch_youtube_source_reuses_metadata_cache(
        self,
        _resolve,
        run_metadata,
        hydrate_metadata,
    ):
        source = {
            "id": "training-data-podcast",
            "type": "podcast",
            "name": "Training Data",
            "url": "https://www.youtube.com/playlist?list=abc",
            "platform": "youtube",
            "topics": ["llm"],
            "transcript": {"enabled": False},
        }
        payload = {
            "entries": [
                {
                    "id": "abc123",
                    "title": "Cached Episode",
                    "webpage_url": "https://www.youtube.com/watch?v=abc123",
                    "upload_date": "20260504",
                }
            ]
        }
        cache = {
            "metadata": {
                fetch_podcast.metadata_cache_key(source): {
                    "payload": payload,
                    "ts": time.time(),
                }
            },
            "transcripts": {},
        }

        episodes = fetch_podcast.fetch_youtube_source(
            source,
            utc("2026-05-01T00:00:00Z"),
            cache,
            no_cache=False,
        )

        run_metadata.assert_not_called()
        hydrate_metadata.assert_not_called()
        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "Cached Episode")

    def test_save_podcast_cache_suppresses_replace_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "podcast-cache.json"
            with patch.object(fetch_podcast, "PODCAST_CACHE_PATH", str(cache_path)):
                with patch("fetch_podcast.os.replace", side_effect=OSError("replace failed")) as replace:
                    fetch_podcast.save_podcast_cache({"transcripts": {}})

            leftovers = list(Path(tmpdir).glob("follow-news-podcast-cache-*"))

        replace.assert_called_once()
        self.assertEqual(leftovers, [])

    @patch("fetch_podcast.fetch_rss_source", side_effect=RuntimeError("network failed"))
    def test_fetch_source_returns_error_result_on_fetch_failure(self, _fetch_rss):
        source = {
            "id": "test-podcast",
            "type": "podcast",
            "name": "Test Podcast",
            "url": "https://example.com/feed.xml",
            "topics": ["llm"],
        }
        cutoff = utc("2026-05-01T00:00:00Z")

        result = fetch_podcast.fetch_source(source, cutoff, {"transcripts": {}}, no_cache=True)

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["articles"], [])
        self.assertIn("network failed", result["error"])

    @patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=0,
            stdout="{not-json",
            stderr="",
        ),
    )
    def test_run_ytdlp_metadata_raises_on_invalid_json(self, _run):
        source = {"url": "https://www.youtube.com/playlist?list=abc"}

        with self.assertRaises(RuntimeError):
            fetch_podcast.run_ytdlp_metadata("/usr/local/bin/yt-dlp", source)

    @patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=1,
            stdout="",
            stderr="metadata failed",
        ),
    )
    def test_run_ytdlp_metadata_raises_on_nonzero_exit(self, _run):
        source = {"url": "https://www.youtube.com/playlist?list=abc"}

        with self.assertRaises(RuntimeError):
            fetch_podcast.run_ytdlp_metadata("/usr/local/bin/yt-dlp", source)

    @patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=0,
            stdout='{"entries":[]}',
            stderr="",
        ),
    )
    def test_run_ytdlp_metadata_uses_flat_playlist_discovery(self, run):
        source = {"url": "https://www.youtube.com/playlist?list=abc"}

        fetch_podcast.run_ytdlp_metadata("/usr/local/bin/yt-dlp", source)

        cmd = run.call_args.args[0]
        self.assertIn("--flat-playlist", cmd)
        self.assertIn("--playlist-items", cmd)
        self.assertEqual(
            cmd[cmd.index("--playlist-items") + 1],
            "1:20,-20:",
        )

    @patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=["yt-dlp"],
            returncode=0,
            stdout='{"id":"abc123","upload_date":"20260504"}',
            stderr="",
        ),
    )
    def test_run_ytdlp_video_metadata_uses_no_playlist_skip_download(self, run):
        fetch_podcast.run_ytdlp_video_metadata(
            "/usr/local/bin/yt-dlp",
            "https://www.youtube.com/watch?v=abc123",
        )

        cmd = run.call_args.args[0]
        self.assertIn("--skip-download", cmd)
        self.assertIn("--no-playlist", cmd)
        self.assertNotIn("--flat-playlist", cmd)

    def test_output_cache_is_fresh_rejects_arbitrary_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "podcast.json"
            output.write_text(json.dumps({"ok": True}), encoding="utf-8")

            self.assertFalse(fetch_podcast.output_cache_is_fresh(output))

    def test_output_cache_is_fresh_accepts_valid_podcast_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "podcast.json"
            params = {
                "defaults": str(Path(tmpdir) / "defaults"),
                "config": None,
                "hours": 336,
                "no_cache": False,
            }
            output.write_text(
                json.dumps(
                    {
                        "source_type": "podcast",
                        "sources": [],
                        "input_params": params,
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(fetch_podcast.output_cache_is_fresh(output, params))

    def test_output_cache_is_fresh_rejects_different_runtime_params(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "podcast.json"
            params = {
                "defaults": str(Path(tmpdir) / "defaults"),
                "config": None,
                "hours": 1,
                "no_cache": False,
            }
            output.write_text(
                json.dumps(
                    {
                        "source_type": "podcast",
                        "sources": [],
                        "input_params": params,
                    }
                ),
                encoding="utf-8",
            )

            expected = {**params, "hours": 336}

            self.assertFalse(fetch_podcast.output_cache_is_fresh(output, expected))

    def test_output_cache_is_fresh_rejects_missing_runtime_params_when_expected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "podcast.json"
            expected = {
                "defaults": str(Path(tmpdir) / "defaults"),
                "config": None,
                "hours": 336,
                "no_cache": False,
            }
            output.write_text(
                json.dumps(
                    {
                        "source_type": "podcast",
                        "sources": [],
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(fetch_podcast.output_cache_is_fresh(output, expected))

    def test_output_request_params_change_when_config_contents_change(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            defaults = Path(tmpdir) / "defaults"
            config = Path(tmpdir) / "config"
            defaults.mkdir()
            config.mkdir()
            (defaults / "sources.json").write_text(
                json.dumps({"sources": []}),
                encoding="utf-8",
            )
            overlay = config / "follow-news-sources.json"
            overlay.write_text(
                json.dumps({"sources": [{"id": "one"}]}),
                encoding="utf-8",
            )

            first = fetch_podcast.output_request_params(defaults, config, 336)
            overlay.write_text(
                json.dumps({"sources": [{"id": "two"}]}),
                encoding="utf-8",
            )
            second = fetch_podcast.output_request_params(defaults, config, 336)

        self.assertEqual(first["defaults"], second["defaults"])
        self.assertEqual(first["config"], second["config"])
        self.assertNotEqual(first["config_fingerprint"], second["config_fingerprint"])

    @patch("fetch_podcast.save_podcast_cache")
    @patch("fetch_podcast.run_fetch", return_value=0)
    @patch("fetch_podcast.load_podcast_sources", return_value=[])
    def test_main_no_cache_does_not_save_cache(self, _load_sources, _run_fetch, save_cache):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "podcast.json"
            argv = [
                "fetch-podcast.py",
                "--defaults",
                str(Path(tmpdir) / "defaults"),
                "--output",
                str(output),
                "--no-cache",
                "--force",
            ]
            with patch.object(sys, "argv", argv):
                result = fetch_podcast.main()

        self.assertEqual(result, 0)
        save_cache.assert_not_called()
