#!/usr/bin/env python3
"""Acceptance tests for final Markdown/Discord digest output."""

import json
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).parent.parent
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"
ACCEPTANCE_FIXTURE = FIXTURES_DIR / "acceptance-merged.json"


def load_acceptance_fixture():
    with open(ACCEPTANCE_FIXTURE, "r") as f:
        return json.load(f)


class TestAcceptanceFixture(unittest.TestCase):
    def test_fixture_covers_acceptance_sections(self):
        data = load_acceptance_fixture()
        self.assertEqual(data["output_stats"]["total_articles"], 9)

        articles = [
            article
            for topic in data["topics"].values()
            for article in topic.get("articles", [])
        ]
        source_types = {article.get("source_type") for article in articles}

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


if __name__ == "__main__":
    unittest.main()
