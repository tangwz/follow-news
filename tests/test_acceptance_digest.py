#!/usr/bin/env python3
"""Acceptance tests for final Markdown/Discord digest output."""

import difflib
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"
GOLDEN_DIR = ROOT_DIR / "tests" / "golden"
TOPICS_FILE = ROOT_DIR / "config" / "defaults" / "topics.json"
ACCEPTANCE_FIXTURE = FIXTURES_DIR / "acceptance-merged.json"
DAILY_GOLDEN = GOLDEN_DIR / "daily-discord.md"
DAILY_CHAT_GOLDEN = GOLDEN_DIR / "daily-chat.md"
FIXED_DIGEST_SECTIONS = {
    "## 📢 KOL Updates",
    "## 📦 GitHub Releases",
    "## 🐙 GitHub Trending",
    "## 📝 Blog Picks",
    "## 🎙️ Podcast Remix",
}

spec = importlib.util.spec_from_file_location(
    "render_acceptance_digest",
    SCRIPTS_DIR / "render-acceptance-digest.py",
)
render_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_mod)


def load_acceptance_fixture():
    return json.loads(ACCEPTANCE_FIXTURE.read_text(encoding="utf-8"))


def render_daily_digest():
    data = load_acceptance_fixture()
    topic_defs = render_mod.load_topic_definitions(TOPICS_FILE)
    return render_mod.render_digest(
        data,
        topic_defs,
        report_date="2026-02-27",
        version="3.17.0",
    )


def render_daily_chat_digest():
    data = load_acceptance_fixture()
    topic_defs = render_mod.load_topic_definitions(TOPICS_FILE)
    return render_mod.render_digest(
        data,
        topic_defs,
        report_date="2026-02-27",
        version="3.17.0",
        template="chat",
    )


def duplicate_visible_fixture():
    return {
        "input_sources": {},
        "output_stats": {"total_articles": 3},
        "topics": {
            "llm": {
                "articles": [
                    {
                        "title": "Independent blog post about agent evals",
                        "link": "https://example.com/blog/agent-evals?utm_source=rss",
                        "quality_score": 12,
                        "source_type": "rss",
                        "source_name": "Example Blog",
                        "author": "Example Author",
                        "is_blog_pick": True,
                        "summary": "The post explains a compact agent evaluation pattern.",
                    },
                    {
                        "title": "Agent planning podcast episode",
                        "link": "https://www.youtube.com/watch?v=abc123&utm_source=rss",
                        "quality_score": 11,
                        "source_type": "podcast",
                        "source_name": "Training Data",
                        "show_name": "Training Data",
                        "transcript_status": "ok",
                        "transcript": "The episode discusses planning, evaluation, and rollout checks.",
                        "snippet": "A podcast episode about planning and evaluation.",
                    },
                ]
            },
            "ai-agent": {
                "articles": [
                    {
                        "title": "KOL note about agent reliability",
                        "link": "https://x.com/example/status/1?utm_source=rss",
                        "quality_score": 10,
                        "source_type": "twitter",
                        "source_name": "Example Lab",
                        "display_name": "Example Lab",
                        "handle": "example",
                        "summary": "Example Lab shared a note about agent reliability.",
                        "metrics": {
                            "impression_count": 1000,
                            "reply_count": 2,
                            "retweet_count": 3,
                            "like_count": 4,
                        },
                    }
                ]
            },
        },
    }


def topic_topic_duplicate_fixture():
    duplicate_article = {
        "title": "Duplicate agent evals article",
        "link": "https://example.com/shared/agent-evals",
        "quality_score": 12,
        "source_type": "rss",
        "summary": "A shared article that appears in two topic sections.",
    }
    return {
        "input_sources": {},
        "output_stats": {"total_articles": 2},
        "topics": {
            "llm": {"articles": [duplicate_article]},
            "ai-agent": {"articles": [dict(duplicate_article)]},
        },
    }


def same_title_duplicate_fixture():
    return {
        "input_sources": {},
        "output_stats": {"total_articles": 2},
        "topics": {
            "llm": {
                "articles": [
                    {
                        "title": "Same Story",
                        "link": "https://example.com/a",
                        "quality_score": 12,
                        "source_type": "rss",
                        "summary": "The first visible instance of the story.",
                    }
                ]
            },
            "ai-agent": {
                "articles": [
                    {
                        "title": "Same Story!",
                        "link": "https://other.example/b",
                        "quality_score": 11,
                        "source_type": "rss",
                        "summary": "The duplicate story with a different URL.",
                    }
                ]
            },
        },
    }


def extract_chat_summary(text, title_line):
    lines = text.splitlines()
    index = lines.index(title_line)
    return lines[index + 2]


def assert_or_update_golden(testcase, expected_path, actual):
    if os.environ.get("UPDATE_GOLDEN") == "1":
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(actual, encoding="utf-8")
        print(f"golden updated: {expected_path}")
        return

    if not expected_path.exists():
        raise AssertionError(
            f"Golden file is missing: {expected_path}. "
            "Run with UPDATE_GOLDEN=1 to create it."
        )

    expected = expected_path.read_text(encoding="utf-8")
    if actual != expected:
        diff = "".join(
            difflib.unified_diff(
                expected.splitlines(keepends=True),
                actual.splitlines(keepends=True),
                fromfile=str(expected_path),
                tofile="rendered daily digest",
            )
        )
        testcase.fail(f"Golden mismatch for {expected_path}:\n" + diff)


class TestAcceptanceFixture(unittest.TestCase):
    def test_fixture_covers_acceptance_sections(self):
        data = load_acceptance_fixture()
        self.assertEqual(data["output_stats"]["total_articles"], 9)
        self.assertEqual(data["output_stats"]["topics_count"], len(data["topics"]))

        articles = [
            article
            for topic in data["topics"].values()
            for article in topic.get("articles", [])
        ]
        actual_topic_distribution = {
            topic_name: len(topic.get("articles", []))
            for topic_name, topic in data["topics"].items()
        }
        source_types = {article.get("source_type") for article in articles}

        self.assertEqual(
            data["output_stats"]["topic_distribution"],
            actual_topic_distribution,
        )
        self.assertIn("rss", source_types)
        self.assertIn("twitter", source_types)
        self.assertIn("github", source_types)
        self.assertIn("github_trending", source_types)
        self.assertIn("podcast", source_types)
        self.assertTrue(any(article.get("multi_source") for article in articles))
        self.assertTrue(any(article.get("is_blog_pick") for article in articles))
        self.assertTrue(
            any(
                article.get("transcript_status") == "ok"
                and article.get("transcript")
                for article in articles
            )
        )


class TestVisibleArticleDedupe(unittest.TestCase):
    def test_article_dedupe_key_normalizes_equivalent_urls(self):
        first = {
            "title": "First title",
            "link": "https://www.youtube.com/watch?v=abc123&utm_source=rss",
        }
        second = {
            "title": "Different title",
            "link": "https://youtu.be/abc123",
        }

        self.assertEqual(
            render_mod.article_dedupe_key(first),
            render_mod.article_dedupe_key(second),
        )

    def test_article_dedupe_key_preserves_meaningful_query_parameters(self):
        first = {"link": "https://news.ycombinator.com/item?id=1"}
        second = {"link": "https://news.ycombinator.com/item?id=2"}

        self.assertNotEqual(
            render_mod.article_dedupe_key(first),
            render_mod.article_dedupe_key(second),
        )

    def test_article_dedupe_key_drops_tracking_query_parameters(self):
        first = {"link": "https://example.com/post?utm_source=rss&fbclid=x"}
        second = {"link": "https://www.example.com/post?utm_medium=email"}

        self.assertEqual(
            render_mod.article_dedupe_key(first),
            render_mod.article_dedupe_key(second),
        )

    def test_article_dedupe_key_keeps_meaningful_query_without_tracking_noise(self):
        first = {"link": "https://example.com/post?id=1&utm_source=rss"}
        second = {"link": "https://www.example.com/post?utm_medium=email&id=1"}

        self.assertEqual(
            render_mod.article_dedupe_key(first),
            render_mod.article_dedupe_key(second),
        )

    def test_visible_registry_matches_same_title_with_different_urls(self):
        registry = render_mod.VisibleArticleRegistry()
        registry.mark({"title": "Same Story", "link": "https://example.com/a"})

        self.assertTrue(
            registry.is_seen(
                {"title": "Same Story!", "link": "https://other.example/b"}
            )
        )

    def test_article_dedupe_key_keeps_versioned_titles_distinct(self):
        registry = render_mod.VisibleArticleRegistry()
        registry.mark(
            {"title": "OpenAI releases GPT-5", "link": "https://example.com/gpt-5"}
        )

        self.assertFalse(
            registry.is_seen(
                {
                    "title": "OpenAI releases GPT-4",
                    "link": "https://example.com/gpt-4",
                }
            )
        )

    def test_article_dedupe_key_keeps_hyphenated_titles_intact(self):
        self.assertEqual(
            render_mod.normalize_visible_title("State-of-the-art agents"),
            "stateoftheart agents",
        )

    def test_article_dedupe_key_strips_source_suffixes(self):
        title = "OpenAI releases GPT-5"

        self.assertEqual(
            render_mod.normalize_visible_title(f"{title} | Example News"),
            render_mod.normalize_visible_title(title),
        )
        self.assertEqual(
            render_mod.normalize_visible_title(f"{title} - Example News"),
            render_mod.normalize_visible_title(title),
        )
        self.assertEqual(
            render_mod.normalize_visible_title(f"{title} – Example News"),
            render_mod.normalize_visible_title(title),
        )

    def test_article_dedupe_key_falls_back_to_normalized_title(self):
        first = {"title": "RT @user: OpenAI releases GPT-5!"}
        second = {"title": "OpenAI releases GPT-5"}

        self.assertEqual(
            render_mod.article_dedupe_key(first),
            render_mod.article_dedupe_key(second),
        )

    def test_article_dedupe_key_returns_none_without_url_or_title(self):
        self.assertIsNone(render_mod.article_dedupe_key({"source_type": "rss"}))


class TestAcceptanceRenderer(unittest.TestCase):
    def test_daily_digest_matches_golden(self):
        assert_or_update_golden(self, DAILY_GOLDEN, render_daily_digest())

    def test_daily_chat_digest_matches_golden(self):
        assert_or_update_golden(self, DAILY_CHAT_GOLDEN, render_daily_chat_digest())

    def test_daily_chat_digest_structure_contract(self):
        text = render_daily_chat_digest()
        lines = text.splitlines()

        self.assertTrue(text.startswith("# 🚀 Tech Digest - 2026-02-27\n"))
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertIn("1. 🧠 [9/10] OpenAI ships structured agent evaluation suite", text)
        self.assertIn("🔗 https://openai.com/research/agent-evals", text)
        self.assertNotIn("<https://", text)
        self.assertNotIn("Low scoring model rumor should not render", text)

        title_lines = [
            line
            for line in lines
            if re.match(r"^[0-9]+\. .+ \[[0-9]+(?:\.[0-9]+)?/10\] .+", line)
        ]
        self.assertGreater(len(title_lines), 0)

        for index, line in enumerate(lines):
            if not re.match(r"^[0-9]+\. .+ \[[0-9]+(?:\.[0-9]+)?/10\] .+", line):
                continue
            self.assertLess(index + 4, len(lines))
            self.assertEqual(lines[index + 1], "")
            self.assertNotEqual(lines[index + 2], "")
            self.assertEqual(lines[index + 3], "")
            self.assertRegex(lines[index + 4], r"^🔗 https?://.+$")

        section_starts = [
            index for index, line in enumerate(lines) if line.startswith("## ")
        ]
        for position, start in enumerate(section_starts):
            end = (
                section_starts[position + 1]
                if position + 1 < len(section_starts)
                else len(lines)
            )
            section_text = "\n".join(lines[start:end])
            self.assertRegex(
                section_text,
                r"(?m)^[0-9]+\. .+ \[[0-9]+(?:\.[0-9]+)?/10\] .+",
                lines[start],
            )

    def test_chat_non_github_summaries_keep_stable_evidence_phrases(self):
        text = render_daily_chat_digest()

        expected_phrases = [
            "结构化评测",
            "LangGraph 新增 checkpoint",
            "prompt injection",
            "product taste",
            "SWE-bench 分享了一份",
        ]

        for phrase in expected_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, text)

    def test_digest_prompt_documents_non_github_summary_contract(self):
        prompt = (ROOT_DIR / "references" / "digest-prompt.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("### Non-GitHub Summary Quality Contract", prompt)
        self.assertIn("This contract applies to KOL, non-GitHub topic, Blog Picks, Reddit, and Podcast items.", prompt)
        self.assertIn("It does not apply to GitHub Releases or GitHub Trending.", prompt)
        self.assertIn("Use a tendency-based structure", prompt)
        self.assertIn("full_text > summary > snippet > title", prompt)
        self.assertIn("Lower-priority fields may provide supplemental context", prompt)
        self.assertIn("metrics.impression_count", prompt)
        self.assertIn("metrics.reply_count", prompt)
        self.assertIn("metrics.retweet_count", prompt)
        self.assertIn("metrics.like_count", prompt)
        self.assertIn("Missing, null, empty, or unparsable metric values render as 0.", prompt)
        self.assertIn("Discord and email length limits take precedence over sentence-count targets.", prompt)

    def test_templates_document_non_github_summary_contract(self):
        template_paths = [
            ROOT_DIR / "references" / "templates" / "chat.md",
            ROOT_DIR / "references" / "templates" / "discord.md",
            ROOT_DIR / "references" / "templates" / "email.md",
        ]

        for template_path in template_paths:
            with self.subTest(template=template_path.name):
                text = template_path.read_text(encoding="utf-8")
                self.assertIn("Non-GitHub Summary Quality", text)
                self.assertIn("KOL, non-GitHub topic, Blog Picks, Reddit, and Podcast", text)
                self.assertIn("GitHub Releases and GitHub Trending keep their existing concise style.", text)
                self.assertIn("Use a tendency-based structure", text)
                self.assertIn("full_text > summary > snippet > title", text)
                self.assertIn("Lower-priority fields may provide supplemental context", text)
                self.assertIn("length limits take precedence over sentence-count targets", text)

    def test_chat_digest_filters_linkless_items_and_empty_topics(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 2},
            "topics": {
                "llm": {
                    "articles": [
                        {
                            "title": "No link item",
                            "quality_score": 18,
                            "source_type": "rss",
                            "chat_summary": "This item must not render because it has no link.",
                        }
                    ]
                },
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Visible agent item",
                            "link": "https://example.com/agent",
                            "quality_score": 10,
                            "source_type": "rss",
                            "chat_summary": "This item remains visible and uses fallback scoring.",
                        }
                    ]
                },
            },
        }
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        self.assertNotIn("## 🧠 LLM / Large Models", text)
        self.assertNotIn("No link item", text)
        self.assertIn("## 🤖 AI Agent", text)
        self.assertIn("1. 🤖 [5/10] Visible agent item", text)
        self.assertIn("🔗 https://example.com/agent", text)

    def test_chat_digest_invalid_score_uses_zero_fallback(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Invalid score item",
                            "link": "https://example.com/invalid-score",
                            "quality_score": "NaN",
                            "source_type": "rss",
                            "chat_summary": "This item remains visible with fallback scoring.",
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        self.assertIn("1. 🤖 [0/10] Invalid score item", text)

    def test_default_discord_digest_filters_invalid_score_items(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Invalid score should not render in Discord",
                            "link": "https://example.com/invalid-discord",
                            "quality_score": "NaN",
                            "source_type": "rss",
                            "summary": "This item must not render in Discord.",
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="discord",
        )

        self.assertNotIn("Invalid score should not render in Discord", text)
        self.assertNotIn("https://example.com/invalid-discord", text)

    def test_chat_digest_skips_missing_null_and_empty_scores(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 3},
            "topics": {
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Missing score item",
                            "link": "https://example.com/missing-score",
                            "source_type": "rss",
                            "chat_summary": "This item must not render.",
                        },
                        {
                            "title": "Null score item",
                            "link": "https://example.com/null-score",
                            "quality_score": None,
                            "source_type": "rss",
                            "chat_summary": "This item must not render.",
                        },
                        {
                            "title": "Empty score item",
                            "link": "https://example.com/empty-score",
                            "quality_score": "",
                            "source_type": "rss",
                            "chat_summary": "This item must not render.",
                        },
                    ]
                }
            },
        }
        topic_defs = [{"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        self.assertNotIn("## 🤖 AI Agent", text)
        self.assertNotIn("Missing score item", text)
        self.assertNotIn("Null score item", text)
        self.assertNotIn("Empty score item", text)

    def test_chat_summary_uses_available_material_without_extra_facts(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "llm": {
                    "articles": [
                        {
                            "title": "Snippet-only model note",
                            "link": "https://example.com/snippet",
                            "quality_score": 10,
                            "source_type": "web",
                            "snippet": "Only this snippet is available.",
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )
        summary = extract_chat_summary(
            text,
            "1. 🧠 [5/10] Snippet-only model note",
        )

        self.assertEqual(summary, "Only this snippet is available.")
        self.assertNotIn("OpenAI", summary)
        self.assertNotIn("Anthropic", summary)
        self.assertNotIn("2026", summary)
        self.assertNotIn("CEO", summary)

    def test_chat_summary_prefers_full_text_over_lower_priority_fields(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "builder": {
                    "articles": [
                        {
                            "title": "Title-level fallback only",
                            "link": "https://example.com/priority",
                            "quality_score": 10,
                            "source_type": "rss",
                            "full_text": "Full text evidence should win.",
                            "summary": "Summary evidence should not win.",
                            "snippet": "Snippet evidence should not win.",
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "builder", "emoji": "🏗️", "label": "Builder"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )
        summary = extract_chat_summary(
            text,
            "1. 🏗️ [5/10] Title-level fallback only",
        )

        self.assertEqual(summary, "Full text evidence should win.")

    def test_chat_podcast_without_transcript_does_not_create_transcript_insight(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "podcast": {
                    "articles": [
                        {
                            "title": "Agent taste preview",
                            "link": "https://example.com/podcast",
                            "quality_score": 12,
                            "source_type": "podcast",
                            "show_name": "Training Data",
                            "transcript_status": "missing",
                            "snippet": "A short preview for an upcoming episode.",
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "podcast", "emoji": "🎧", "label": "Podcast"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        self.assertIn("A short preview for an upcoming episode.", text)

    def test_chat_twitter_metrics_are_not_rendered_without_summary_support(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Agent benchmark post",
                            "link": "https://x.com/example/status/1",
                            "quality_score": 11,
                            "source_type": "twitter",
                            "display_name": "Example Lab",
                            "handle": "example",
                            "summary": "Example Lab shared a benchmark note.",
                            "metrics": {
                                "impression_count": 12500,
                                "reply_count": 45,
                                "retweet_count": 230,
                                "like_count": 1800,
                            },
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        self.assertIn("Example Lab shared a benchmark note.", text)
        topic_text = text.split("## 📢 KOL Updates", 1)[0]
        self.assertNotIn("12.5K", topic_text)
        self.assertNotIn("views", topic_text.lower())

    def test_chat_kol_metrics_render_zero_for_missing_values(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Sparse KOL post",
                            "link": "https://x.com/example/status/2",
                            "quality_score": 11,
                            "source_type": "twitter",
                            "display_name": "Example Lab",
                            "handle": "example",
                            "summary": "Example Lab shared a sparse benchmark note.",
                            "metrics": {
                                "impression_count": None,
                                "reply_count": "",
                                "retweet_count": "not-a-number",
                                "like_count": 0,
                            },
                        }
                    ]
                }
            },
        }
        topic_defs = []

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        kol_text = text.split("## 📢 KOL Updates", 1)[1]
        self.assertIn("`👁 0 | 💬 0 | 🔁 0 | ❤️ 0`", kol_text)

    def test_chat_kol_metrics_render_zero_for_invalid_metrics_container(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "ai-agent": {
                    "articles": [
                        {
                            "title": "Invalid metrics container",
                            "link": "https://x.com/example/status/3",
                            "quality_score": 11,
                            "source_type": "twitter",
                            "display_name": "Example Lab",
                            "handle": "example",
                            "summary": "Example Lab shared another benchmark note.",
                            "metrics": None,
                        }
                    ]
                }
            },
        }
        topic_defs = []

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        kol_text = text.split("## 📢 KOL Updates", 1)[1]
        self.assertIn("`👁 0 | 💬 0 | 🔁 0 | ❤️ 0`", kol_text)

    def test_chat_kol_updates_skips_empty_section(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 1},
            "topics": {
                "builder": {
                    "articles": [
                        {
                            "title": "Builder article",
                            "link": "https://example.com/builder",
                            "quality_score": 10,
                            "source_type": "rss",
                            "chat_summary": "Builder evidence remains visible.",
                        }
                    ]
                }
            },
        }
        topic_defs = [{"id": "builder", "emoji": "🏗️", "label": "Builder"}]

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
            template="chat",
        )

        self.assertIn("## 🏗️ Builder", text)
        self.assertNotIn("## 📢 KOL Updates", text)

    def test_daily_digest_structure_contract(self):
        text = render_daily_digest()
        lines = text.splitlines()

        self.assertTrue(text.startswith("# 🚀 Tech Digest - 2026-02-27\n"))
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertIn("---\n", text)
        self.assertIn("Powered by OpenClaw", text)

        article_lines = [line for line in lines if line.startswith("• 🔥")]
        self.assertGreater(len(article_lines), 0)
        for index, line in enumerate(lines):
            if not line.startswith("• 🔥"):
                continue
            self.assertRegex(line, r"^• 🔥[0-9]+(?:\.[0-9]+)? \| .+")
            self.assertLess(index + 1, len(lines))
            self.assertRegex(lines[index + 1], r"^  🔗 https?://.+$")

        section_starts = [
            index for index, line in enumerate(lines) if line.startswith("## ")
        ]
        for position, start in enumerate(section_starts):
            section_title = lines[start]
            if section_title in FIXED_DIGEST_SECTIONS:
                continue

            end = (
                section_starts[position + 1]
                if position + 1 < len(section_starts)
                else len(lines)
            )
            scores = [
                float(match.group(1))
                for line in lines[start:end]
                for match in [re.match(r"^• 🔥([0-9]+(?:\.[0-9]+)?) \| ", line)]
                if match
            ]
            self.assertGreater(len(scores), 0, section_title)
            self.assertEqual(scores, sorted(scores, reverse=True), section_title)

    def test_update_golden_requires_explicit_environment_flag(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            expected_path = Path(tmp_dir) / "golden.md"
            expected_path.write_text("old\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(AssertionError):
                    assert_or_update_golden(self, expected_path, "new\n")
                self.assertEqual(expected_path.read_text(encoding="utf-8"), "old\n")

            with patch.dict(os.environ, {"UPDATE_GOLDEN": "1"}, clear=True):
                assert_or_update_golden(self, expected_path, "new\n")
                self.assertEqual(expected_path.read_text(encoding="utf-8"), "new\n")

    def test_render_digest_uses_current_discord_structure(self):
        data = load_acceptance_fixture()
        topic_defs = render_mod.load_topic_definitions(TOPICS_FILE)

        text = render_mod.render_digest(
            data,
            topic_defs,
            report_date="2026-02-27",
            version="3.17.0",
        )

        self.assertIn("# 🚀 Tech Digest - 2026-02-27", text)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertIn("• 🔥18 | OpenAI ships structured agent evaluation suite", text)
        self.assertIn("  🔗 https://openai.com/research/agent-evals", text)
        self.assertIn("  *[3 sources]*", text)
        self.assertNotIn("Low scoring model rumor should not render", text)
        self.assertNotIn("## 📢 KOL Updates", text)
        self.assertNotIn("## 📦 GitHub Releases", text)
        self.assertNotIn("## 🐙 GitHub Trending", text)
        self.assertNotIn("## 📝 Blog Picks", text)
        self.assertNotIn("## 🎙️ Podcast Remix", text)
        self.assertIn(
            "📊 Data Sources: RSS 3 | Twitter 1 | Reddit 1 | Web 1 | GitHub 1 releases + 1 trending | Podcast 1 episodes | Dedup: 9 articles",
            text,
        )
        self.assertTrue(text.endswith("\n"))

    def test_discord_fixed_sections_render_unseen_items(self):
        data = {
            "input_sources": {},
            "output_stats": {"total_articles": 5},
            "topics": {
                "supplemental": {
                    "articles": [
                        {
                            "title": "Unseen KOL update",
                            "link": "https://x.com/example/status/unseen",
                            "source_type": "twitter",
                            "display_name": "Example Lab",
                            "handle": "example",
                            "summary": "Example Lab shared an unseen update.",
                            "metrics": {
                                "impression_count": 1200,
                                "reply_count": 3,
                                "retweet_count": 4,
                                "like_count": 50,
                            },
                        },
                        {
                            "title": "Example Tool v1.0.0",
                            "link": "https://github.com/example/tool/releases/tag/v1.0.0",
                            "source_type": "github",
                            "repo": "example/tool",
                            "tag_name": "v1.0.0",
                            "summary": "The release ships a stable API.",
                        },
                        {
                            "title": "example/trending-tool",
                            "link": "https://github.com/example/trending-tool",
                            "source_type": "github_trending",
                            "repo": "example/trending-tool",
                            "stars": 2500,
                            "daily_stars_est": 125,
                            "language": "TypeScript",
                            "description": "A trending tool for builders.",
                        },
                        {
                            "title": "Unseen blog pick",
                            "link": "https://example.com/unseen-blog",
                            "source_type": "rss",
                            "is_blog_pick": True,
                            "author": "Example Author",
                            "full_text": "A detailed unseen blog post.",
                        },
                        {
                            "title": "Unseen podcast episode",
                            "link": "https://www.youtube.com/watch?v=unseenpodcast",
                            "source_type": "podcast",
                            "transcript_status": "ok",
                            "transcript": "Host | 00:00 - 00:05 This is an unseen podcast.",
                            "show_name": "Example Show",
                            "snippet": "An unseen podcast episode.",
                        },
                    ]
                }
            },
        }

        text = render_mod.render_digest(
            data,
            topic_defs=[],
            report_date="2026-05-17",
            version="3.17.0",
        )

        self.assertIn("## 📢 KOL Updates", text)
        self.assertIn("## 📦 GitHub Releases", text)
        self.assertIn("## 🐙 GitHub Trending", text)
        self.assertIn("## 📝 Blog Picks", text)
        self.assertIn("## 🎙️ Podcast Remix", text)
        self.assertEqual(text.count("https://x.com/example/status/unseen"), 1)
        self.assertEqual(
            text.count("https://github.com/example/tool/releases/tag/v1.0.0"),
            1,
        )
        self.assertEqual(text.count("https://github.com/example/trending-tool"), 1)
        self.assertEqual(text.count("https://example.com/unseen-blog"), 1)
        self.assertEqual(
            text.count("https://www.youtube.com/watch?v=unseenpodcast"),
            1,
        )

    def test_discord_visible_dedupe_keeps_topic_sections_over_fixed_sections(self):
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            duplicate_visible_fixture(),
            topic_defs,
            report_date="2026-05-17",
            version="3.17.0",
        )

        self.assertEqual(text.count("https://example.com/blog/agent-evals?utm_source=rss"), 1)
        self.assertEqual(text.count("https://www.youtube.com/watch?v=abc123&utm_source=rss"), 1)
        self.assertEqual(text.count("https://x.com/example/status/1?utm_source=rss"), 1)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertIn("## 🤖 AI Agent", text)
        self.assertNotIn("## 📢 KOL Updates", text)
        self.assertNotIn("## 📝 Blog Picks", text)
        self.assertNotIn("## 🎙️ Podcast Remix", text)

    def test_discord_visible_dedupe_skips_duplicate_topic_sections(self):
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            topic_topic_duplicate_fixture(),
            topic_defs,
            report_date="2026-05-17",
            version="3.17.0",
        )

        self.assertEqual(text.count("https://example.com/shared/agent-evals"), 1)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertNotIn("## 🤖 AI Agent", text)

    def test_discord_visible_dedupe_skips_same_title_different_url_topic(self):
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            same_title_duplicate_fixture(),
            topic_defs,
            report_date="2026-05-17",
            version="3.17.0",
        )

        self.assertEqual(text.count("https://example.com/a"), 1)
        self.assertNotIn("https://other.example/b", text)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertNotIn("## 🤖 AI Agent", text)

    def test_chat_visible_dedupe_keeps_topic_sections_over_fixed_sections(self):
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            duplicate_visible_fixture(),
            topic_defs,
            report_date="2026-05-17",
            version="3.17.0",
            template="chat",
        )

        self.assertEqual(text.count("https://example.com/blog/agent-evals?utm_source=rss"), 1)
        self.assertEqual(text.count("https://www.youtube.com/watch?v=abc123&utm_source=rss"), 1)
        self.assertEqual(text.count("https://x.com/example/status/1?utm_source=rss"), 1)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertIn("## 🤖 AI Agent", text)
        self.assertNotIn("## 📢 KOL Updates", text)
        self.assertNotIn("## 📝 Blog Picks", text)
        self.assertNotIn("## 🎙️ Podcast Remix", text)

    def test_chat_visible_dedupe_skips_duplicate_topic_sections(self):
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            topic_topic_duplicate_fixture(),
            topic_defs,
            report_date="2026-05-17",
            version="3.17.0",
            template="chat",
        )

        self.assertEqual(text.count("https://example.com/shared/agent-evals"), 1)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertNotIn("## 🤖 AI Agent", text)

    def test_chat_visible_dedupe_skips_same_title_different_url_topic(self):
        topic_defs = [
            {"id": "llm", "emoji": "🧠", "label": "LLM / Large Models"},
            {"id": "ai-agent", "emoji": "🤖", "label": "AI Agent"},
        ]

        text = render_mod.render_digest(
            same_title_duplicate_fixture(),
            topic_defs,
            report_date="2026-05-17",
            version="3.17.0",
            template="chat",
        )

        self.assertEqual(text.count("https://example.com/a"), 1)
        self.assertNotIn("https://other.example/b", text)
        self.assertIn("## 🧠 LLM / Large Models", text)
        self.assertNotIn("## 🤖 AI Agent", text)

    def test_prepare_manual_codex_context(self):
        data = load_acceptance_fixture()
        topic_defs = render_mod.load_topic_definitions(TOPICS_FILE)
        source_fixture = ACCEPTANCE_FIXTURE

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            render_mod.prepare_codex_acceptance_context(
                data,
                topic_defs,
                source_fixture,
                output_dir,
                report_date="2026-02-27",
                version="3.17.0",
            )

            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"merged.json", "summarized.txt", "prompt.md", "expected.md"},
            )
            self.assertIn(
                "Do not run the network pipeline.",
                (output_dir / "prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Save the generated report as `actual.md` in this directory.",
                (output_dir / "prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "diff -u expected.md actual.md",
                (output_dir / "prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "references/digest-prompt.md",
                (output_dir / "prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "references/templates/discord.md",
                (output_dir / "prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "expected.md as a deterministic comparison sample",
                (output_dir / "prompt.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "# 🚀 Tech Digest - 2026-02-27",
                (output_dir / "expected.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "Total articles: 9",
                (output_dir / "summarized.txt").read_text(encoding="utf-8"),
            )

    def test_cli_prepare_codex_context_does_not_require_output(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_dir = Path(tmp_dir)

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "render-acceptance-digest.py"),
                    "--input",
                    str(ACCEPTANCE_FIXTURE),
                    "--topics",
                    str(TOPICS_FILE),
                    "--date",
                    "2026-02-27",
                    "--version",
                    "3.17.0",
                    "--prepare-codex-context",
                    str(output_dir),
                ],
                cwd=ROOT_DIR,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                {path.name for path in output_dir.iterdir()},
                {"merged.json", "summarized.txt", "prompt.md", "expected.md"},
            )

    def test_cli_can_render_chat_template(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / "daily-chat.md"

            result = subprocess.run(
                [
                    "python3",
                    str(SCRIPTS_DIR / "render-acceptance-digest.py"),
                    "--input",
                    str(ACCEPTANCE_FIXTURE),
                    "--topics",
                    str(TOPICS_FILE),
                    "--date",
                    "2026-02-27",
                    "--version",
                    "3.17.0",
                    "--template",
                    "chat",
                    "--output",
                    str(output_path),
                ],
                cwd=ROOT_DIR,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            text = output_path.read_text(encoding="utf-8")
            self.assertIn("1. 🧠 [9/10] OpenAI ships structured agent evaluation suite", text)
            self.assertIn("🔗 https://openai.com/research/agent-evals", text)
            self.assertNotIn("<https://", text)


if __name__ == "__main__":
    unittest.main()
