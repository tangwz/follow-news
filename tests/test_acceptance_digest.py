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


def assert_or_update_golden(testcase, expected_path, actual):
    if os.environ.get("UPDATE_GOLDEN") == "1":
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(actual, encoding="utf-8")
        print(f"golden updated: {expected_path}")
        return

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
        testcase.fail("Daily digest golden mismatch:\n" + diff)


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
            self.assertRegex(lines[index + 1], r"^  <https?://.+>$")

        llm_start = lines.index("## 🧠 LLM / Large Models")
        llm_end = next(
            index
            for index in range(llm_start + 1, len(lines))
            if lines[index].startswith("## ")
        )
        llm_scores = [
            float(match.group(1))
            for line in lines[llm_start:llm_end]
            for match in [re.match(r"^• 🔥([0-9]+(?:\.[0-9]+)?) \| ", line)]
            if match
        ]
        self.assertGreater(len(llm_scores), 0)
        self.assertEqual(llm_scores, sorted(llm_scores, reverse=True))

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


if __name__ == "__main__":
    unittest.main()
