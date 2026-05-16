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
        self.assertIn("## 📦 GitHub Releases", text)
        self.assertIn("## 🐙 GitHub Trending", text)
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

        self.assertIn("Only this snippet is available.", text)

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
        self.assertNotIn("12.5K", text)
        self.assertNotIn("views", text.lower())

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
        self.assertIn("## 📢 KOL Updates", text)
        self.assertIn("`👁 12.5K | 💬 45 | 🔁 230 | ❤️ 1.8K`", text)
        self.assertIn("## 📦 GitHub Releases", text)
        self.assertIn("## 🐙 GitHub Trending", text)
        self.assertIn("## 📝 Blog Picks", text)
        self.assertIn("## 🎙️ Podcast Remix", text)
        self.assertIn(
            "📊 Data Sources: RSS 3 | Twitter 1 | Reddit 1 | Web 1 | GitHub 1 releases + 1 trending | Podcast 1 episodes | Dedup: 9 articles",
            text,
        )
        self.assertTrue(text.endswith("\n"))

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
