#!/usr/bin/env python3
"""Tests for summarize-merged.py."""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

spec = importlib.util.spec_from_file_location(
    "summarize_merged", SCRIPTS_DIR / "summarize-merged.py"
)
summarize_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summarize_mod)


class TestPodcastSummary(unittest.TestCase):
    def test_ok_status_without_transcript_is_not_ready(self):
        self.assertEqual(
            summarize_mod.display_transcript_status(
                {
                    "transcript_status": "ok",
                    "transcript": "",
                }
            ),
            "ok",
        )

    def test_ready_transcript_is_displayed_for_ok_transcript(self):
        data = {
            "output_stats": {"total_articles": 1},
            "topics": {
                "llm": {
                    "articles": [
                        {
                            "title": "Waymo Autonomy",
                            "source_name": "Training Data",
                            "source_type": "podcast",
                            "show_name": "Training Data",
                            "quality_score": 10,
                            "transcript_status": "ok",
                            "transcript": "Speaker | 00:00 - 00:05 Autonomy is a product problem.",
                            "duration_seconds": 3600,
                        }
                    ]
                }
            },
        }

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            summarize_mod.summarize(data)

        text = output.getvalue()
        self.assertIn("[podcast] Waymo Autonomy", text)
        self.assertIn("Podcast: Training Data · transcript=ready", text)
        self.assertIn("Duration: 3600s", text)


class TestSummaryMaterial(unittest.TestCase):
    def test_truncate_text_normalizes_whitespace_and_adds_ellipsis(self):
        text = "Alpha\n\nBeta\tGamma Delta"

        result = summarize_mod.truncate_text(text, 16)

        self.assertEqual(result, "Alpha Beta Gamma...")

    def test_truncate_text_returns_empty_string_for_missing_text(self):
        self.assertEqual(summarize_mod.truncate_text(None, 20), "")
        self.assertEqual(summarize_mod.truncate_text("", 20), "")

    def test_select_summary_material_prefers_full_text(self):
        article = {
            "title": "Fallback title",
            "snippet": "Snippet text",
            "summary": "Summary text",
            "full_text": "Full text wins",
        }

        result = summarize_mod.select_summary_material(article, max_chars=80)

        self.assertEqual(result, ("full_text", "Full text wins"))

    def test_select_summary_material_falls_back_to_title(self):
        article = {"title": "Only title is available"}

        result = summarize_mod.select_summary_material(article, max_chars=80)

        self.assertEqual(result, ("title", "Only title is available"))

    def test_format_metric_count_uses_compact_units(self):
        self.assertEqual(summarize_mod.format_metric_count(999), "999")
        self.assertEqual(summarize_mod.format_metric_count(1200), "1.2K")
        self.assertEqual(summarize_mod.format_metric_count(2_500_000), "2.5M")

    def test_format_twitter_metrics_returns_all_four_metrics(self):
        metrics = {
            "impression_count": 12345,
            "reply_count": 12,
            "retweet_count": 345,
            "like_count": 6789,
        }

        result = summarize_mod.format_twitter_metrics(metrics)

        self.assertEqual(result, "views=12.3K, replies=12, reposts=345, likes=6.8K")


class TestRenderedEvidence(unittest.TestCase):
    def render_summary(self, article):
        data = {
            "output_stats": {"total_articles": 1},
            "topics": {
                "llm": {
                    "articles": [article],
                }
            },
        }

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            summarize_mod.summarize(data, top_n=1)
        return output.getvalue()

    def test_rss_article_prints_summary_material_and_multi_source_context(self):
        text = "OpenAI released a new model with stronger coding behavior. " * 20
        output = self.render_summary(
            {
                "title": "OpenAI model update",
                "link": "https://example.com/model",
                "source_name": "Example RSS",
                "source_type": "rss",
                "quality_score": 11,
                "full_text": text,
                "multi_source": True,
                "source_count": 3,
                "all_sources": ["Example RSS", "Hacker News", "Reddit"],
            }
        )

        self.assertIn("Summary material (full_text): OpenAI released", output)
        self.assertIn("Multi-source: 3 sources · Example RSS, Hacker News, Reddit", output)

    def test_twitter_article_prints_author_and_all_metrics(self):
        output = self.render_summary(
            {
                "title": "AI coding workflows are changing",
                "link": "https://x.com/person/status/1",
                "source_name": "@person",
                "source_type": "twitter",
                "display_name": "Product Builder",
                "handle": "person",
                "quality_score": 10,
                "metrics": {
                    "impression_count": 12345,
                    "reply_count": 12,
                    "retweet_count": 345,
                    "like_count": 6789,
                },
            }
        )

        self.assertIn("Author: Product Builder (@person)", output)
        self.assertIn("Twitter/X: views=12.3K, replies=12, reposts=345, likes=6.8K", output)

    def test_reddit_article_prints_discussion_context(self):
        output = self.render_summary(
            {
                "title": "Developers debate local-first agents",
                "link": "https://reddit.com/r/programming/comments/1",
                "source_name": "r/programming",
                "source_type": "reddit",
                "quality_score": 9,
                "score": 321,
                "num_comments": 88,
                "flair": "Discussion",
            }
        )

        self.assertIn("Reddit: r/programming · 321↑ · 88 comments · flair=Discussion", output)

    def test_podcast_article_prints_bounded_transcript_excerpt_when_ready(self):
        transcript = "Speaker | 00:00 - 00:05 Autonomy is a product problem. " * 20
        output = self.render_summary(
            {
                "title": "Waymo Autonomy",
                "link": "https://example.com/podcast",
                "source_name": "Training Data",
                "source_type": "podcast",
                "show_name": "Training Data",
                "quality_score": 10,
                "transcript_status": "ok",
                "transcript": transcript,
                "duration_seconds": 3600,
            }
        )

        self.assertIn("Podcast: Training Data · transcript=ready", output)
        self.assertIn("Transcript excerpt: Speaker | 00:00 - 00:05 Autonomy", output)


if __name__ == "__main__":
    unittest.main()
