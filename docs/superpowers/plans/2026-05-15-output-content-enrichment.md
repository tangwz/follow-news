# Output Content Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enrich non-GitHub digest summaries with source-aware context while preserving existing titles, links, ordering, and GitHub Releases / GitHub Trending behavior.

**Architecture:** Keep the pipeline unchanged and improve only the report-generation boundary. `scripts/summarize-merged.py` exposes richer bounded evidence for the final writer, and `references/digest-prompt.md` defines source-specific writing rules that consume that evidence.

**Tech Stack:** Python 3 standard library, `unittest`, existing Markdown prompt templates.

---

## File Structure

- Modify `scripts/summarize-merged.py`: add small formatting helpers and print richer source material inside the existing per-article summary loop.
- Modify `tests/test_summarize_merged.py`: add focused unit tests for helper behavior and rendered summary output.
- Modify `references/digest-prompt.md`: add source-aware summary instructions and update the report-generation guidance without changing GitHub Releases or GitHub Trending rules.

No new runtime dependency is needed. No fetcher, merger, quality scoring, config, or delivery code should change.

## Implementation Notes

- Keep `source_type == "github"` and `source_type == "github_trending"` out of the new enrichment-specific prompt rules.
- `summarize-merged.py` may still print ordinary title, source, and link for GitHub entries as it does today.
- Use deterministic truncation for long text. Prefer `full_text`, then `summary`, then `snippet`, then `title`.
- Keep all helper functions pure where possible so tests can exercise them without subprocesses.

---

### Task 1: Add Summary Material Helpers

**Files:**
- Modify: `scripts/summarize-merged.py`
- Test: `tests/test_summarize_merged.py`

- [ ] **Step 1: Write failing helper tests**

Add these tests to `TestPodcastSummary` or a new `TestSummaryMaterial` class in `tests/test_summarize_merged.py`:

```python
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
```

- [ ] **Step 2: Run helper tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_summarize_merged.py -v
```

Expected: fail with missing attributes such as `truncate_text`, `select_summary_material`, `format_metric_count`, or `format_twitter_metrics`.

- [ ] **Step 3: Implement helper functions**

Add these functions near the top of `scripts/summarize-merged.py`, after imports and before `display_transcript_status`:

```python
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


def select_summary_material(article: dict, max_chars: int = 500) -> tuple[str, str]:
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
```

- [ ] **Step 4: Run helper tests to verify pass**

Run:

```bash
python3 -m unittest tests/test_summarize_merged.py -v
```

Expected: pass for the new helper tests and existing podcast tests.

- [ ] **Step 5: Commit helper changes**

Run:

```bash
git add scripts/summarize-merged.py tests/test_summarize_merged.py
git commit -m "test: cover digest summary material helpers"
```

---

### Task 2: Print Richer Evidence in summarize-merged Output

**Files:**
- Modify: `scripts/summarize-merged.py`
- Test: `tests/test_summarize_merged.py`

- [ ] **Step 1: Write failing rendered-output tests**

Add these tests to `tests/test_summarize_merged.py`:

```python
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
```

- [ ] **Step 2: Run rendered-output tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_summarize_merged.py -v
```

Expected: fail because the new output lines are not printed yet.

- [ ] **Step 3: Implement richer output lines**

In `scripts/summarize-merged.py`, update the per-article loop inside `summarize()` after the existing `Snippet:` printing block. Keep the current title, source, link, metrics, Reddit, and podcast lines unless the code below replaces the less complete metric or Reddit formatting.

Use this block after `if snippet:`:

```python
            field_name, summary_material = select_summary_material(a)
            if summary_material and summary_material != snippet:
                print(f"      Summary material ({field_name}): {summary_material}")

            handle = a.get("handle") or a.get("username") or a.get("screen_name")
            if source_type == "twitter" and display_name:
                author = display_name
                if handle:
                    author = f"{author} (@{handle})"
                print(f"      Author: {author}")

            if a.get("multi_source") and a.get("source_count"):
                source_names = a.get("all_sources") or []
                if source_names:
                    print(
                        "      Multi-source: "
                        f"{a['source_count']} sources · {', '.join(source_names[:5])}"
                    )
                else:
                    print(f"      Multi-source: {a['source_count']} sources")
```

Replace the current generic metrics printing block with:

```python
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
```

Replace the current Reddit-specific block with:

```python
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
```

Inside the existing `if source_type == "podcast":` block, after the duration line, add:

```python
                if transcript_status == "ready":
                    transcript_excerpt = truncate_text(a.get("transcript"), 600)
                    if transcript_excerpt:
                        print(f"      Transcript excerpt: {transcript_excerpt}")
```

- [ ] **Step 4: Run summary tests to verify pass**

Run:

```bash
python3 -m unittest tests/test_summarize_merged.py -v
```

Expected: all tests in `tests/test_summarize_merged.py` pass.

- [ ] **Step 5: Commit richer summary output**

Run:

```bash
git add scripts/summarize-merged.py tests/test_summarize_merged.py
git commit -m "feat: expose source-aware digest evidence"
```

---

### Task 3: Add Source-Aware Prompt Rules

**Files:**
- Modify: `references/digest-prompt.md`

- [ ] **Step 1: Inspect the report-generation area**

Run:

```bash
sed -n '45,150p' references/digest-prompt.md
```

Expected: output includes the existing `Report Generation`, `Executive Summary`, `Topic Sections`, and fixed-section rules.

- [ ] **Step 2: Insert source-aware summary guidance**

In `references/digest-prompt.md`, insert this section after the paragraph that starts with `Select articles purely by quality_score` and before `Each article line must include its quality score`:

```markdown
### Source-Aware Summary Style

Keep every selected item's original title and link unchanged. Enrich only the summary or description text after the title.

Use the evidence printed by `summarize-merged.py`, especially `Summary material`, `Author`, `Twitter/X`, `Reddit`, `Podcast`, `Transcript excerpt`, and `Multi-source` lines. Do not invent details beyond that evidence. If the available material is thin, write a shorter and more cautious summary.

Apply these source-specific styles:

- `twitter`: Write 2-4 Chinese sentences. Identify the person or organization, explain the key claim or action, and add practical context about why it matters. Prefer concrete claims over generic paraphrases. Include metrics only when they help explain significance.
- `rss` and `web`: Write 80-150 Chinese characters when only snippet-level material is available, and up to 150-220 Chinese characters when `full_text` material is available. Include the core fact, technical or product detail, and likely impact.
- `reddit`: Write 2-3 Chinese sentences. Distinguish the linked story from the community reaction. Include subreddit, score, and comment count when useful.
- `podcast`: For items with `transcript=ready`, write 150-300 Chinese characters with the core takeaway, speaker or show context, 2-4 concrete insights, and one short quote from the transcript. For items without a usable transcript, write only metadata-backed summaries from title, show name, snippet, duration, and source metadata.

Do not apply this enrichment style to `source_type == "github"` or `source_type == "github_trending"`. The GitHub Releases and GitHub Trending fixed sections below keep their existing rules.
```

- [ ] **Step 3: Tighten topic section wording**

In the `Topic Sections` area, replace the current item description guidance with this wording:

```markdown
For each selected topic article, keep the title and link from the source item. Write the description using the source-aware style above. The description should add concrete context, not merely translate or repeat the title.
```

Keep the existing ordering and minimum-score warnings unchanged.

- [ ] **Step 4: Confirm GitHub fixed sections still state unchanged behavior**

Run:

```bash
rg -n "GitHub Releases|GitHub Trending|source_type == \"github\"|source_type == \"github_trending\"" references/digest-prompt.md
```

Expected: output includes the new exclusion line and the existing fixed sections for `GitHub Releases` and `GitHub Trending`.

- [ ] **Step 5: Commit prompt changes**

Run:

```bash
git add references/digest-prompt.md
git commit -m "docs: add source-aware digest summary rules"
```

---

### Task 4: Final Verification

**Files:**
- Verify: `scripts/summarize-merged.py`
- Verify: `tests/test_summarize_merged.py`
- Verify: `references/digest-prompt.md`

- [ ] **Step 1: Run focused unit tests**

Run:

```bash
python3 -m unittest tests/test_summarize_merged.py -v
```

Expected: all tests pass.

- [ ] **Step 2: Run summary helper against the fixture**

Run:

```bash
python3 scripts/summarize-merged.py --input tests/fixtures/merged.json --top 2
```

Expected: command exits successfully and prints `Summary material`, `Reddit`, or other source-aware context lines for non-GitHub items when the fixture contains the corresponding fields.

- [ ] **Step 3: Confirm GitHub prompt rules were not rewritten**

Run:

```bash
sed -n '/GitHub Releases/,/Blog Picks/p' references/digest-prompt.md
```

Expected: GitHub Releases still says to show all releases and has no quality-score prefix. GitHub Trending still says to show top 5 plus any additional repos with `daily_stars_est > 50`.

- [ ] **Step 4: Check worktree status**

Run:

```bash
git status --short
```

Expected: no uncommitted files after the task commits.

---

## Self-Review

Spec coverage:

- Non-GitHub content enrichment is covered by Task 2 and Task 3.
- Existing title and link preservation is covered by Task 3 prompt wording.
- GitHub Releases and GitHub Trending exclusions are covered by Task 3 and Task 4.
- Bounded source material exposure is covered by Task 1 and Task 2.
- Focused tests are covered by Task 1, Task 2, and Task 4.

Placeholder scan:

- The plan contains no unresolved placeholders.
- Every code-changing step includes concrete code or exact text to insert.
- Every verification step includes the command and expected result.

Type consistency:

- Helper names are consistent across tests and implementation: `truncate_text`, `select_summary_material`, `format_metric_count`, and `format_twitter_metrics`.
- The implementation uses existing article field names from the merged JSON shape: `full_text`, `summary`, `snippet`, `display_name`, `metrics`, `score`, `num_comments`, `show_name`, `transcript_status`, and `transcript`.
