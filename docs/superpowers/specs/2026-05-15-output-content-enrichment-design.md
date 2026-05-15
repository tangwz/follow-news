# Output Content Enrichment Design

## Goal

Improve the digest output for all non-GitHub release and non-GitHub trending content. The existing title and link for each item must remain unchanged. The enrichment applies only to the summary or description text after the title.

The output should follow the useful parts of the `zarazhangrui/follow-builders` prompt style: source-aware summaries, stronger context, more concrete details, and stricter factual boundaries.

## Scope

In scope:

- Enrich topic section summaries for RSS, web, Reddit, Twitter/X, and podcast items.
- Enrich the fixed KOL, Blog Picks, and Podcast Remix sections.
- Expose more source material through `scripts/summarize-merged.py` so the final digest prompt has enough context.
- Update `references/digest-prompt.md` with source-specific writing rules.
- Preserve current article ordering, quality score thresholds, title text, and source links.

Out of scope:

- Changing GitHub Releases formatting or selection.
- Changing GitHub Trending formatting or selection.
- Changing fetchers, source configuration, deduplication, or quality scoring.
- Adding a new LLM enrichment pipeline stage.
- Rewriting output delivery templates beyond minimal wording alignment if needed.

## Recommended Approach

Use a two-layer enhancement:

1. `scripts/summarize-merged.py` should expose richer but bounded material for each candidate item.
2. `references/digest-prompt.md` should instruct the final report writer to use source-specific summary styles.

This keeps the change local to the report generation boundary. It avoids changing source collection, ranking, or delivery behavior, while giving the LLM enough evidence to write less shallow summaries.

## Source-Specific Summary Rules

### Twitter/X

Write 2-4 sentences. The summary should identify the person or organization, explain the key claim or action, and add context about why it matters.

Use available fields:

- `display_name`
- `source_name`
- tweet text from title or snippet
- metrics such as impressions, replies, reposts, and likes
- link

The style should resemble:

`Peter Yang ... pointed out ... He argues ... The practical implication is ...`

Avoid merely translating the tweet title. Avoid inventing background that is not present in the available material.

### RSS and Web

Write 80-150 Chinese characters when source material is limited, and up to 150-220 Chinese characters when `full_text` is available. Summaries should include the core fact, technical or product detail, and likely impact.

Prefer concrete details:

- company, project, or person names
- model names, version numbers, benchmark names, dates, and metrics
- what changed compared with the prior state
- who is affected

If `full_text` is available, use it as the primary source. If only `snippet` is available, keep the summary more cautious.

### Reddit

Write 2-3 sentences. Distinguish the linked story from the community reaction. Include subreddit, score, and comment count when available.

For self posts, summarize the discussion prompt and the strongest visible angle. For link posts, summarize the source article first, then mention community traction.

### Podcast

For podcast items with `transcript_status == "ok"` and a non-empty transcript, write 150-300 Chinese characters. Include the core takeaway, speaker or show context, 2-4 concrete insights, and one short quote from the transcript.

For podcast items without a usable transcript, do not synthesize claims beyond title, show name, snippet, duration, and metadata. These items can be mentioned only as metadata-backed items.

### GitHub Releases and GitHub Trending

Keep existing behavior unchanged. The enrichment rules above do not apply to:

- `source_type == "github"`
- `source_type == "github_trending"`

## Data Exposure Changes

`scripts/summarize-merged.py` should continue printing a human-readable overview, but each item should include enough evidence for richer digest writing:

- `Summary material`: a bounded excerpt from `full_text`, `summary`, or `snippet`.
- `Source context`: source name, source type, display name, handle, show name, or subreddit where available.
- `Engagement context`: Twitter/X metrics and Reddit score/comment counts.
- `Podcast context`: transcript status, duration, show name, and a bounded transcript excerpt when usable.
- `Multi-source context`: source count and source names when an item was merged across sources.

The helper should keep output size controlled. Long fields should be truncated deterministically, with enough text to support summaries but not enough to make the command output unwieldy.

## Prompt Changes

`references/digest-prompt.md` should add a dedicated "Source-Aware Summary Style" section near report generation. It should state:

- Keep original titles and links unchanged.
- Enrich only the summary or description text.
- Use the summary material printed by `summarize-merged.py`.
- Apply the source-specific rules above.
- Do not apply these enrichment rules to GitHub Releases or GitHub Trending.
- Do not invent details beyond the available source material.
- If the available material is thin, write a shorter and more cautious summary.

The existing ranking and ordering rules remain authoritative:

- Keep quality-score descending order.
- Keep minimum score threshold for topic sections.
- Keep existing fixed-section selection rules unless explicitly changed later.

## Output Shape

The final digest should still have the same visible structure. Example shape:

```markdown
• 🔥15 | Original Article Title — richer source-aware summary that explains the concrete event, technical detail, and why it matters.
  <https://example.com/original-link>
```

For KOL-style items:

```markdown
• **Display Name** (@handle) — source-aware summary that gives the person context, key claim, and practical implication. `👁 12.3K | 💬 45 | 🔁 230 | ❤️ 1.2K`
  <https://x.com/handle/status/id>
```

## Error Handling and Fallbacks

If a field is missing:

- Fall back from `full_text` to `summary`, then to `snippet`, then to title-only.
- Avoid printing empty labels.
- Avoid pretending transcript-backed detail exists when transcript is missing.
- Keep links from existing `link`, `reddit_url`, or `external_url` fallback logic.

If source material is too thin:

- Keep the item short.
- State only what the title and source metadata support.
- Do not pad the summary with generic commentary.

## Testing

Recommended tests:

- Update `tests/test_summarize_merged.py` to verify richer fields are printed for RSS/web items with `full_text`.
- Add a Twitter/X fixture case that checks display name, metrics, and summary material appear.
- Add a Reddit fixture case that checks score and comment context appear.
- Keep the existing podcast transcript readiness tests.
- Add or update a podcast test that checks transcript excerpts are bounded and only shown when transcript is usable.
- Ensure GitHub Releases and GitHub Trending behavior remains unchanged by avoiding new enrichment assertions for those source types.

Recommended command:

```bash
python3 -m unittest tests/test_summarize_merged.py
```

## Risks

- Richer summaries can make Discord output exceed message limits more often. Existing chunking rules should remain in force.
- Exposing too much `full_text` or transcript text can make the summary helper noisy. Deterministic truncation is required.
- Prompt-only enforcement may still vary by LLM. Better source material improves reliability, but the final summary remains generated text.

## Acceptance Criteria

- Non-GitHub content includes richer source-aware summaries.
- Existing titles and links are preserved.
- GitHub Releases and GitHub Trending output remains unchanged.
- `summarize-merged.py` exposes enough material for source-aware writing without dumping entire articles or transcripts.
- The prompt clearly defines source-specific summary lengths and factual boundaries.
- Focused tests cover the summary helper behavior.
