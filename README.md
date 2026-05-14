# Follow News

> Automated tech news digest — 156 built-in sources, 7-source pipeline, one chat message to install.

**English** | [中文](README_CN.md)

[![Tests](https://github.com/tangwz/follow-news/actions/workflows/test.yml/badge.svg)](https://github.com/tangwz/follow-news/actions/workflows/test.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![ClawHub](https://img.shields.io/badge/ClawHub-follow--news-blueviolet)](https://clawhub.com/tangwz/follow-news)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 💬 Install in One Message

Tell your [OpenClaw](https://openclaw.ai) AI assistant:

> **"Install follow-news and send a daily digest every morning at 9am"**

That's it. Your bot handles installation, configuration, scheduling, and delivery — all through conversation.

More examples:

> 🗣️ "Set up a weekly AI digest, only `llm` and `ai-agent` topics, deliver to Discord #ai-weekly every Monday"

> 🗣️ "Install follow-news, add my RSS feeds, and include the `builder` and `kol` topics"

> 🗣️ "Give me a tech digest right now, skip Twitter sources"

Or install via CLI:
```bash
clawhub install follow-news
```

## 📊 What You Get

A quality-scored, deduplicated tech digest built from **156 built-in sources** plus **5 web search topics**:

| Layer | Sources | What |
|-------|---------|------|
| 📡 RSS | 65 feeds | OpenAI, Simon Willison, Hugging Face, HN, 36氪… |
| 🐦 Twitter/X | 60 KOLs | @sama, @karpathy, @paulg, @garrytan, @dotey… |
| 🔍 Web Search | 5 topics | `llm`, `ai-agent`, `builder`, `kol`, `frontier-tech` with freshness filters |
| 🐙 GitHub | 23 repos | Releases from key projects (LangChain, vLLM, DeepSeek, Llama…) |
| 🗣️ Reddit | 8 subs | r/MachineLearning, r/LocalLLaMA, r/OpenAI, r/ExperiencedDevs… |
| 🎙️ Podcast | custom sources | RSS podcast feeds and YouTube playlists/channels with optional transcripts |

### Pipeline

```
       run-pipeline.py (~30s)
              ↓
  RSS ────────┐
  Twitter ────┤
  Web ────────┤── parallel fetch ──→ merge-sources.py
  GitHub ─────┤                          ↓
  GitHub Tr. ─┤              enrich-articles.py (opt-in)
  Reddit ─────┤                          ↓
  Podcast ────┘
              Quality Scoring → Dedup → Topic Grouping
                             ↓
               Discord / Email / PDF output
```

**Quality scoring**: priority source (+3), multi-source cross-ref (+5), recency (+2), engagement (+1), Reddit score bonus (+1/+3/+5), already reported (-5).

## ⚙️ Configuration

- `config/defaults/sources.json` — 156 built-in sources (65 RSS, 60 Twitter, 23 GitHub, 8 Reddit)
- `config/defaults/topics.json` — 5 topics: `llm`, `ai-agent`, `builder`, `kol`, `frontier-tech`
- User overrides in `workspace/config/` take priority

## 🎨 Customize Your Sources

Works out of the box with 156 built-in sources (65 RSS, 60 Twitter, 23 GitHub, 8 Reddit) and supports custom podcast sources — but fully customizable. Copy the defaults to your workspace config and override:

```bash
# Copy and customize
cp config/defaults/sources.json workspace/config/follow-news-sources.json
cp config/defaults/topics.json workspace/config/follow-news-topics.json
```

Your overlay file **merges** with defaults:
- **Override** a source by matching its `id` — your version replaces the default
- **Add** new sources with a unique `id` — appended to the list
- **Disable** a built-in source — set `"enabled": false` on the matching `id`

```json
{
  "sources": [
    {"id": "my-blog", "type": "rss", "enabled": true, "url": "https://myblog.com/feed", "topics": ["llm"]},
    {
      "id": "training-data-podcast",
      "type": "podcast",
      "name": "Training Data",
      "url": "https://www.youtube.com/playlist?list=PLOhHNjZItNnMm5tdW61JpnyxeYH5NDDx8",
      "platform": "youtube",
      "enabled": true,
      "priority": true,
      "topics": ["llm", "ai-agent"],
      "transcript": {
        "enabled": true,
        "backend": "yt-dlp",
        "languages": ["en", "zh", "zh-Hans"]
      }
    },
    {"id": "openai-rss", "enabled": false}
  ]
}
```

No need to copy the entire file — just include what you want to change.

Podcast sources use `type: "podcast"`. RSS podcast feeds work without extra tools. YouTube podcast sources use `platform: "youtube"` and can fetch metadata and transcripts through the optional `yt-dlp` runtime.

## 🔧 Environment Variables

All environment variables are optional. The pipeline runs with whatever sources are available.

```bash
# Twitter/X Backend (auto priority: opencli > getxapi > twitterapiio > official)
export TWITTER_API_BACKEND="auto"  # auto|opencli|getxapi|twitterapiio|official
export OPENCLI_BIN="/path/to/opencli"  # optional; defaults to opencli on PATH
export OPENCLI_MAX_WORKERS="10"  # optional; increase parallel OpenCLI workers
export OPENCLI_AUTO_UPDATE="1"      # auto-update OpenCLI if support exists (default: 1)
export OPENCLI_NO_UPDATE="0"        # set 1 to skip OpenCLI auto-update
export OPENCLI_UPDATE_COMMAND="self-update"  # optional; try this command if auto-update
export OPENCLI_UPDATE_CHECK_INTERVAL_SECONDS="86400"  # optional; defaults to 24h
export OPENCLI_CLOSE_TABS_AFTER_RUN="1"  # optional; close OpenCLI-created X/Twitter tabs after fetch
export OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN="1"  # optional; close Chrome automation windows opened by OpenCLI
export GETX_API_KEY="..."        # GetXAPI fallback
export TWITTERAPI_IO_KEY="..."   # twitterapi.io fallback
export X_BEARER_TOKEN="..."      # Official X API v2 fallback
# Web Search
export TAVILY_API_KEY="tvly-xxx"   # Tavily Search API
export BRAVE_API_KEYS="k1,k2,k3"   # Brave Search API keys (comma-separated for rotation)
export BRAVE_API_KEY="..."         # Single Brave key
export WEB_SEARCH_BACKEND="auto"   # auto|brave|tavily|browser
# GitHub
export GITHUB_TOKEN="..."          # GitHub API
# Podcast transcripts
export YTDLP_BIN="/path/to/yt-dlp"  # optional; defaults to yt-dlp on PATH
# Other
export BRAVE_PLAN="free"           # Override Brave rate limit: free|pro
```

OpenCLI is preferred because it can reuse an authenticated Chrome/Chromium session instead of requiring Twitter API credentials. API backends remain available for CI, headless machines, or users who already configured API keys.

To use the OpenCLI backend, install the OpenCLI executable yourself and make it available on `PATH`, or set `OPENCLI_BIN` to its absolute path. In OpenClaw, also install the `jackwener/opencli` Skill so the agent can run `opencli doctor`, check the browser bridge, and guide X login-state troubleshooting.

OpenCLI browser bridge stability depends on the local browser extension connection. The fetcher defaults to 10 concurrent OpenCLI workers (`OPENCLI_MAX_WORKERS=10`) and has a hard cap at 10. It also closes X/Twitter tabs created during the OpenCLI fetch (`OPENCLI_CLOSE_TABS_AFTER_RUN=1` by default) and, on macOS, closes Chrome automation windows that OpenCLI opened during the run (`OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN=1` by default) while leaving pre-existing windows alone.

RSS podcast feeds do not need extra tools. YouTube podcast metadata and transcript fetching require `yt-dlp`; install it on `PATH`, or set `YTDLP_BIN` to the executable path. If `yt-dlp` is missing, that YouTube podcast source is marked failed without blocking the rest of the pipeline.

## 📦 Dependencies

### Core (required)

The skill requires Python 3.8+ and two optional dependencies for enhanced functionality:

```bash
pip install -r requirements.txt
# or
pip install feedparser>=6.0.0 jsonschema>=4.0.0
```

- **feedparser** — RSS/Atom feed parsing (fallback to regex if not installed)
- **jsonschema** — JSON Schema validation for config files

### Optional

```bash
pip install weasyprint yt-dlp
```

- **weasyprint** — Enables PDF report generation
- **yt-dlp** — Enables YouTube podcast metadata and transcript fetching; `YTDLP_BIN` can point to a standalone binary

## 📂 Repository

**GitHub**: [github.com/tangwz/follow-news](https://github.com/tangwz/follow-news)

## 🌟 Featured In

- [Awesome OpenClaw Use Cases](https://github.com/hesamsheikh/awesome-openclaw-usecases) — Community-curated collection of OpenClaw agent use cases

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
