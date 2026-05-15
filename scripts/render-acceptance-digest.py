#!/usr/bin/env python3
"""Render deterministic Markdown/Discord digest output for acceptance tests."""

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence


MIN_QUALITY_SCORE = 5


def load_json(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_topic_definitions(path: Path) -> List[Dict[str, Any]]:
    data = load_json(path)
    return [topic for topic in data.get("topics", []) if isinstance(topic, dict)]


def article_link(article: Dict[str, Any]) -> str:
    return (
        article.get("link")
        or article.get("external_url")
        or article.get("reddit_url")
        or ""
    )


def quality_score(article: Dict[str, Any]) -> float:
    value = article.get("quality_score", 0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def format_score(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def format_count(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0

    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M".replace(".0M", "M")
    if number >= 1_000:
        return f"{number / 1_000:.1f}K".replace(".0K", "K")
    return str(number)


def iter_articles(data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for topic in data.get("topics", {}).values():
        if not isinstance(topic, dict):
            continue
        for article in topic.get("articles", []):
            if isinstance(article, dict):
                yield article


def unique_articles(
    articles: Iterable[Dict[str, Any]],
    source_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    seen = set()
    result = []
    for article in articles:
        if source_type and article.get("source_type") != source_type:
            continue
        key = article_link(article) or article.get("title") or id(article)
        if key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def sorted_topic_articles(topic_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    articles = [
        article
        for article in topic_data.get("articles", [])
        if isinstance(article, dict)
        and quality_score(article) >= MIN_QUALITY_SCORE
        and article_link(article)
    ]
    return sorted(articles, key=quality_score, reverse=True)


def render_topic_sections(
    data: Dict[str, Any],
    topic_defs: Sequence[Dict[str, Any]],
) -> List[str]:
    sections = []
    topics = data.get("topics", {})

    for topic_def in topic_defs:
        topic_id = topic_def.get("id")
        topic_data = topics.get(topic_id)
        if not isinstance(topic_data, dict):
            continue

        articles = sorted_topic_articles(topic_data)
        if not articles:
            continue

        lines = [
            f"## {topic_def.get('emoji', '')} {topic_def.get('label', topic_id)}".rstrip(),
            "",
        ]
        for article in articles:
            score = format_score(quality_score(article))
            lines.append(f"• 🔥{score} | {article.get('title', '?')}")
            lines.append(f"  <{article_link(article)}>")
            if article.get("multi_source"):
                lines.append(f"  *[{article.get('source_count', 2)} sources]*")
            lines.append("")
        sections.append("\n".join(lines).rstrip())

    return sections


def render_kol_updates(data: Dict[str, Any]) -> Optional[str]:
    tweets = [
        article
        for article in unique_articles(iter_articles(data), "twitter")
        if article_link(article)
    ]
    if not tweets:
        return None

    tweets = sorted(tweets, key=quality_score, reverse=True)
    lines = ["## 📢 KOL Updates", ""]
    for article in tweets:
        metrics = article.get("metrics", {})
        metric_text = (
            f"👁 {format_count(metrics.get('impression_count'))} | "
            f"💬 {format_count(metrics.get('reply_count'))} | "
            f"🔁 {format_count(metrics.get('retweet_count'))} | "
            f"❤️ {format_count(metrics.get('like_count'))}"
        )
        display_name = (
            article.get("display_name") or article.get("source_name") or "Unknown"
        )
        handle = article.get("handle") or article.get("source_id") or "unknown"
        summary = article.get("summary") or article.get("snippet") or article.get(
            "title", ""
        )
        lines.append(f"• **{display_name}** (@{handle}) — {summary} `{metric_text}`")
        lines.append(f"  <{article_link(article)}>")
        lines.append("")

    return "\n".join(lines).rstrip()


def render_github_releases(data: Dict[str, Any]) -> Optional[str]:
    releases = [
        article
        for article in unique_articles(iter_articles(data), "github")
        if article_link(article)
    ]
    if not releases:
        return None

    releases = sorted(releases, key=quality_score, reverse=True)
    lines = ["## 📦 GitHub Releases", ""]
    for article in releases:
        repo = article.get("repo") or article.get("source_name") or article.get(
            "title", "?"
        )
        tag = article.get("tag_name") or article.get("version") or "release"
        summary = article.get("summary") or article.get("snippet") or article.get(
            "title", ""
        )
        lines.append(f"• **{repo}** `{tag}` — {summary}")
        lines.append(f"  <{article_link(article)}>")
        lines.append("")

    return "\n".join(lines).rstrip()


def render_github_trending(data: Dict[str, Any]) -> Optional[str]:
    repos = [
        article
        for article in unique_articles(iter_articles(data), "github_trending")
        if article_link(article)
    ]
    if not repos:
        return None

    repos = sorted(
        repos,
        key=lambda article: article.get("daily_stars_est", 0),
        reverse=True,
    )
    lines = ["## 🐙 GitHub Trending", ""]
    for article in repos:
        repo = article.get("repo") or article.get("title", "?")
        stars = format_count(article.get("stars"))
        daily_stars = format_count(article.get("daily_stars_est"))
        language = article.get("language") or "Unknown"
        description = article.get("description") or article.get("snippet") or ""
        lines.append(
            f"• **{repo}** ⭐ {stars} (+{daily_stars}/day) | "
            f"{language} — {description}"
        )
        lines.append(f"  <{article_link(article)}>")
        lines.append("")

    return "\n".join(lines).rstrip()


def render_blog_picks(data: Dict[str, Any]) -> Optional[str]:
    picks = [
        article
        for article in unique_articles(iter_articles(data))
        if article.get("is_blog_pick") and article_link(article)
    ]
    if not picks:
        return None

    picks = sorted(picks, key=quality_score, reverse=True)
    lines = ["## 📝 Blog Picks", ""]
    for article in picks:
        author = article.get("author") or article.get("source_name") or "Unknown"
        summary = (
            article.get("full_text")
            or article.get("snippet")
            or article.get("summary")
            or ""
        )
        lines.append(f"• **{article.get('title', '?')}** — {author} | {summary}")
        lines.append(f"  <{article_link(article)}>")
        lines.append("")

    return "\n".join(lines).rstrip()


def render_podcast_remix(data: Dict[str, Any]) -> Optional[str]:
    episodes = [
        article
        for article in unique_articles(iter_articles(data), "podcast")
        if article.get("transcript_status") == "ok"
        and article.get("transcript")
        and article_link(article)
    ]
    if not episodes:
        return None

    episodes = sorted(episodes, key=quality_score, reverse=True)
    lines = ["## 🎙️ Podcast Remix", ""]
    for article in episodes:
        show_name = article.get("show_name") or article.get("source_name") or "Unknown"
        summary = article.get("snippet") or article.get("summary") or ""
        quote = str(article.get("transcript", "")).strip().splitlines()[0]
        lines.append(
            f"• **{article.get('title', '?')}** — {show_name} | "
            f'{summary} Quote: "{quote}"'
        )
        lines.append(f"  <{article_link(article)}>")
        lines.append("")

    return "\n".join(lines).rstrip()


def source_count(data: Dict[str, Any], key: str) -> int:
    input_sources = data.get("input_sources", {})
    value = input_sources.get(key, 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def render_footer(data: Dict[str, Any], version: str) -> str:
    stats = data.get("output_stats", {})
    merged = stats.get("total_articles", 0)
    return "\n".join(
        [
            "---",
            (
                "📊 Data Sources: "
                f"RSS {source_count(data, 'rss_articles')} | "
                f"Twitter {source_count(data, 'twitter_articles')} | "
                f"Reddit {source_count(data, 'reddit_posts')} | "
                f"Web {source_count(data, 'web_articles')} | "
                f"GitHub {source_count(data, 'github_articles')} releases + "
                f"{source_count(data, 'trending_repositories')} trending | "
                f"Podcast {source_count(data, 'podcast_episodes')} episodes | "
                f"Dedup: {merged} articles"
            ),
            (
                f"🤖 Generated by follow-news v{version} | "
                "<https://github.com/tangwz/follow-news> | Powered by OpenClaw"
            ),
        ]
    )


def render_digest(
    data: Dict[str, Any],
    topic_defs: Sequence[Dict[str, Any]],
    report_date: str,
    version: str,
) -> str:
    sections = [f"# 🚀 Tech Digest - {report_date}"]
    sections.extend(render_topic_sections(data, topic_defs))

    for renderer in (
        render_kol_updates,
        render_github_releases,
        render_github_trending,
        render_blog_picks,
        render_podcast_remix,
    ):
        section = renderer(data)
        if section:
            sections.append(section)

    sections.append(render_footer(data, version))
    return "\n\n".join(sections).rstrip() + "\n"


def summarize_fixture(data: Dict[str, Any]) -> str:
    stats = data.get("output_stats", {})
    topics = data.get("topics", {})
    total_articles = stats.get("total_articles", 0)

    lines = [
        "Acceptance digest fixture summary",
        f"Total articles: {total_articles}",
        f"Topics: {len(topics)}",
        "",
    ]

    for topic_id in sorted(topics):
        topic_data = topics.get(topic_id, {})
        if not isinstance(topic_data, dict):
            continue

        articles = [
            article
            for article in topic_data.get("articles", [])
            if isinstance(article, dict)
        ]
        lines.append(f"## {topic_id} ({len(articles)} articles)")
        for article in sorted(articles, key=lambda item: item.get("title", "")):
            title = article.get("title", "?")
            source_type = article.get("source_type", "unknown")
            score = format_score(quality_score(article))
            summary = (
                article.get("summary")
                or article.get("snippet")
                or article.get("description")
                or article.get("full_text")
                or ""
            )
            lines.append(f"- [{source_type} score={score}] {title}")
            if summary:
                lines.append(f"  Summary: {summary}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_codex_prompt(report_date: str, version: str) -> str:
    return "\n".join(
        [
            "# Manual Codex Acceptance Context",
            "",
            "Use this folder to review the deterministic digest renderer manually.",
            "",
            "Rules:",
            "- Do not run the network pipeline.",
            "- Use merged.json as the only source fixture.",
            "- Use summarized.txt for a quick fixture overview.",
            (
                "- Follow references/digest-prompt.md and "
                "references/templates/discord.md as the source-of-truth "
                "Markdown and Discord formatting rules."
            ),
            (
                "- Treat expected.md as a deterministic comparison sample, "
                "not as the prompt source of truth."
            ),
            "- Save the generated report as `actual.md` in this directory.",
            "- Compare expected.md vs actual.md with `diff -u expected.md actual.md`.",
            "- Keep golden updates gated by UPDATE_GOLDEN=1.",
            f"- Report date: {report_date}",
            f"- follow-news version: {version}",
            "",
        ]
    )


def prepare_codex_acceptance_context(
    data: Dict[str, Any],
    topic_defs: Sequence[Dict[str, Any]],
    source_fixture: Path,
    output_dir: Path,
    report_date: str,
    version: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(source_fixture, output_dir / "merged.json")
    (output_dir / "summarized.txt").write_text(
        summarize_fixture(data),
        encoding="utf-8",
    )
    (output_dir / "prompt.md").write_text(
        build_codex_prompt(report_date, version),
        encoding="utf-8",
    )
    (output_dir / "expected.md").write_text(
        render_digest(data, topic_defs, report_date, version),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render deterministic Markdown/Discord digest acceptance output."
    )
    parser.add_argument("--input", type=Path, required=True, help="Merged JSON input")
    parser.add_argument("--topics", type=Path, required=True, help="Topics JSON file")
    parser.add_argument("--date", required=True, help="Report date in YYYY-MM-DD format")
    parser.add_argument("--version", required=True, help="follow-news version string")
    parser.add_argument("--output", type=Path, help="Markdown output path")
    parser.add_argument(
        "--prepare-codex-context",
        type=Path,
        help="Directory for manual Codex acceptance context files",
    )
    return parser


def parse_args() -> argparse.Namespace:
    parser = build_parser()
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = load_json(args.input)
    topic_defs = load_topic_definitions(args.topics)
    if args.prepare_codex_context:
        prepare_codex_acceptance_context(
            data,
            topic_defs,
            args.input,
            args.prepare_codex_context,
            args.date,
            args.version,
        )
        return 0

    if not args.output:
        parser = build_parser()
        parser.error("--output is required unless --prepare-codex-context is used")

    output = render_digest(data, topic_defs, args.date, args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
