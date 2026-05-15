#!/usr/bin/env python3
"""
Print a human-readable summary of merged JSON data for LLM consumption.

Usage:
    python3 summarize-merged.py [--input /tmp/td-merged.json] [--top N] [--topic TOPIC]
"""

import json
import argparse
from pathlib import Path
from typing import Tuple


def normalize_whitespace(value: str) -> str:
    """Collapse repeated whitespace for compact terminal output."""
    return " ".join(str(value).split())


def truncate_text(value, max_chars: int = 500) -> str:
    """Return normalized text capped at max_chars."""
    if not value:
        return ""

    normalized = normalize_whitespace(value)
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "..."


def select_summary_material(article: dict, max_chars: int = 500) -> Tuple[str, str]:
    """Pick the richest available text field for digest writing."""
    for field in ("full_text", "summary", "snippet", "title"):
        material = truncate_text(article.get(field), max_chars)
        if material:
            return field, material
    return "", ""


def format_metric_count(value) -> str:
    """Format large engagement counts for human scanning."""
    if value is None:
        return "0"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return "0"

    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}K"
    return str(int(number))


def format_twitter_metrics(metrics: dict) -> str:
    """Return the four Twitter/X metrics used by the digest prompt."""
    if not isinstance(metrics, dict):
        metrics = {}

    return (
        f"views={format_metric_count(metrics.get('impression_count'))}, "
        f"replies={format_metric_count(metrics.get('reply_count'))}, "
        f"reposts={format_metric_count(metrics.get('retweet_count'))}, "
        f"likes={format_metric_count(metrics.get('like_count'))}"
    )


def display_transcript_status(article: dict) -> str:
    """Return the human-facing transcript state for podcast summaries."""
    status = article.get("transcript_status", "missing")
    if status == "ok" and article.get("transcript"):
        return "ready"
    return status


def summarize(data: dict, top_n: int = 10, topic_filter: str = None):
    """Print structured summary of merged data."""
    
    # Metadata
    meta = data.get("output_stats", {})
    print(f"=== Merged Data Summary ===")
    print(f"Total articles: {meta.get('total_articles', '?')}")
    print(f"Topics: {', '.join(data.get('topics', {}).keys())}")
    print()
    
    topics = data.get("topics", {})
    
    for topic_id, topic_data in topics.items():
        if topic_filter and topic_id != topic_filter:
            continue
        
        articles = topic_data.get("articles", [])
        if not isinstance(articles, list):
            continue
        
        print(f"=== {topic_id} ({len(articles)} articles) ===")
        
        # Sort by quality_score descending
        sorted_articles = sorted(
            [a for a in articles if isinstance(a, dict)],
            key=lambda a: a.get("quality_score", 0),
            reverse=True
        )
        
        for i, a in enumerate(sorted_articles[:top_n]):
            title = a.get("title", "?")[:100]
            source = a.get("source_name", "?")
            source_type = a.get("source_type", "?")
            qs = a.get("quality_score", 0)
            link = a.get("link") or a.get("reddit_url") or a.get("external_url", "")
            snippet = (a.get("snippet") or a.get("summary") or "")[:150]
            
            # Metrics for Twitter
            metrics = a.get("metrics", {})
            display_name = a.get("display_name", "")
            rich_evidence_enabled = source_type not in {"github", "github_trending"}
            
            print(f"\n  [{i+1}] ({qs:.0f}pts) [{source_type}] {title}")
            print(f"      Source: {source}", end="")
            if display_name:
                print(f" ({display_name})", end="")
            print()
            if link:
                print(f"      Link: {link}")
            if snippet:
                print(f"      Snippet: {snippet}")
            if rich_evidence_enabled:
                field_name, summary_material = select_summary_material(a)
                if summary_material:
                    print(f"      Summary material ({field_name}): {summary_material}")

            handle = a.get("handle") or a.get("username") or a.get("screen_name")
            if source_type == "twitter" and display_name:
                author = display_name
                if handle:
                    author = f"{author} (@{handle})"
                print(f"      Author: {author}")

            if rich_evidence_enabled and a.get("multi_source") and a.get("source_count"):
                source_names = a.get("all_sources") or []
                if source_names:
                    print(
                        "      Multi-source: "
                        f"{a['source_count']} sources · {', '.join(source_names[:5])}"
                    )
                else:
                    print(f"      Multi-source: {a['source_count']} sources")

            if metrics:
                if source_type == "twitter":
                    print(f"      Twitter/X: {format_twitter_metrics(metrics)}")
                else:
                    parts = []
                    for k, v in metrics.items():
                        if v and v > 0:
                            parts.append(f"{k}={v}")
                    if parts:
                        print(f"      Metrics: {', '.join(parts)}")
            
            # Reddit-specific
            reddit_score = a.get("score")
            num_comments = a.get("num_comments")
            if source_type == "reddit" and reddit_score is not None:
                reddit_parts = [source, f"{reddit_score}↑"]
                if num_comments is not None:
                    reddit_parts.append(f"{num_comments} comments")
                if a.get("flair"):
                    reddit_parts.append(f"flair={a['flair']}")
                print(f"      Reddit: {' · '.join(reddit_parts)}")
            elif reddit_score is not None:
                print(f"      Reddit: {reddit_score}↑", end="")
                if num_comments:
                    print(f" · {num_comments} comments", end="")
                print()

            if source_type == "podcast":
                transcript_status = display_transcript_status(a)
                show_name = a.get("show_name") or source
                print(f"      Podcast: {show_name} · transcript={transcript_status}")
                if a.get("duration_seconds"):
                    print(f"      Duration: {a['duration_seconds']}s")
                if transcript_status == "ready":
                    transcript_excerpt = truncate_text(a.get("transcript"), 600)
                    if transcript_excerpt:
                        print(f"      Transcript excerpt: {transcript_excerpt}")
        
        print()


def main():
    parser = argparse.ArgumentParser(description="Summarize merged JSON for LLM consumption")
    parser.add_argument("--input", "-i", type=Path, default=Path("/tmp/td-merged.json"))
    parser.add_argument("--top", "-n", type=int, default=10, help="Top N articles per topic")
    parser.add_argument("--topic", "-t", type=str, default=None, help="Filter to specific topic")
    args = parser.parse_args()
    
    if not args.input.exists():
        print(f"Error: {args.input} not found. Run the pipeline first.")
        return
    
    with open(args.input) as f:
        data = json.load(f)
    
    summarize(data, top_n=args.top, topic_filter=args.topic)


if __name__ == "__main__":
    main()
