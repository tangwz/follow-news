#!/usr/bin/env python3
"""Acceptance tests for final Markdown/Discord digest output."""

import difflib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).parent.parent
SCRIPTS_DIR = ROOT_DIR / "scripts"
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"
GOLDEN_DIR = ROOT_DIR / "tests" / "golden"
TOPICS_FILE = ROOT_DIR / "config" / "defaults" / "topics.json"
ACCEPTANCE_FIXTURE = FIXTURES_DIR / "acceptance-merged.json"
DAILY_GOLDEN = GOLDEN_DIR / "daily-discord.md"

spec = importlib.util.spec_from_file_location(
    "render_acceptance_digest",
    SCRIPTS_DIR / "render-acceptance-digest.py",
)
render_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render_mod)


def load_acceptance_fixture():
    with open(ACCEPTANCE_FIXTURE, "r") as f:
        return json.load(f)


def render_daily_digest():
    data = load_acceptance_fixture()
    topic_defs = render_mod.load_topic_definitions(TOPICS_FILE)
    return render_mod.render_digest(
        data,
        topic_defs,
        report_date="2026-02-27",
        version="3.17.0",
    )


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
        expected = DAILY_GOLDEN.read_text()
        actual = render_daily_digest()

        if actual != expected:
            diff = "\n".join(
                difflib.unified_diff(
                    expected.splitlines(),
                    actual.splitlines(),
                    fromfile=str(DAILY_GOLDEN),
                    tofile="rendered daily digest",
                    lineterm="",
                )
            )
            self.fail(f"Daily digest golden mismatch:\n{diff}")

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
        self.assertIn("  <https://openai.com/research/agent-evals>", text)
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


if __name__ == "__main__":
    unittest.main()
