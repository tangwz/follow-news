# Follow News

> Automated tech news digest — 168 built-in sources, 6-source pipeline, one chat message to install.

**English** | [中文](README_CN.md)

[![Tests](https://github.com/tangwz/follow-news/actions/workflows/test.yml/badge.svg)](https://github.com/tangwz/follow-news/actions/workflows/test.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![ClawHub](https://img.shields.io/badge/ClawHub-follow--news-blueviolet)](https://clawhub.com/tangwz/follow-news)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 💬 Install in One Message

Tell your [OpenClaw](https://openclaw.ai) AI assistant:

> **"Install follow-news and send a daily digest to #tech-news every morning at 9am"**

That's it. Your bot handles installation, configuration, scheduling, and delivery — all through conversation.

More examples:

> 🗣️ "Set up a weekly AI digest, only LLM and AI Agent topics, deliver to Discord #ai-weekly every Monday"

> 🗣️ "Install follow-news, add my RSS feeds, and send crypto news to Telegram"

> 🗣️ "Give me a tech digest right now, skip Twitter sources"

Or install via CLI:
```bash
clawhub install follow-news
```

## 📊 What You Get

A quality-scored, deduplicated tech digest built from **168 built-in sources** plus **4 web search topics**:

| Layer | Sources | What |
|-------|---------|------|
| 📡 RSS | 78 feeds | OpenAI, Anthropic, Ben's Bites, HN, 36氪, CoinDesk… |
| 🐦 Twitter/X | 48 KOLs | @karpathy, @VitalikButerin, @sama, @elonmusk… |
| 🔍 Web Search | 4 topics | Tavily or Brave Search API with freshness filters |
| 🐙 GitHub | 29 repos | Releases from key projects (LangChain, vLLM, DeepSeek, Llama…) |
| 🗣️ Reddit | 13 subs | r/MachineLearning, r/LocalLLaMA, r/CryptoCurrency… |

### Pipeline

```
       run-pipeline.py (~30s)
              ↓
  RSS ────────┐
  Twitter ────┤
  Web ────────┤── parallel fetch ──→ merge-sources.py
  GitHub ─────┤                          ↓
  GitHub Tr. ─┤              enrich-articles.py (opt-in)
  Reddit ─────┘                          ↓
              Quality Scoring → Dedup → Topic Grouping
                             ↓
               Discord / Email / PDF output
```

**Quality scoring**: priority source (+3), multi-source cross-ref (+5), recency (+2), engagement (+1), Reddit score bonus (+1/+3/+5), already reported (-5).

## ⚙️ Configuration

- `config/defaults/sources.json` — 168 built-in sources (78 RSS, 48 Twitter, 29 GitHub, 13 Reddit)
- `config/defaults/topics.json` — 4 topics with search queries & Twitter queries
- User overrides in `workspace/config/` take priority

## 🎨 Customize Your Sources

Works out of the box with 168 built-in sources (78 RSS, 48 Twitter, 29 GitHub, 13 Reddit) — but fully customizable. Copy the defaults to your workspace config and override:

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
    {"id": "openai-blog", "enabled": false}
  ]
}
```

No need to copy the entire file — just include what you want to change.

## 🔧 Environment Variables

All environment variables are optional. The pipeline runs with whatever sources are available.

```bash
# Twitter/X Backend (auto priority: opencli > getxapi > twitterapiio > official)
export TWITTER_API_BACKEND="auto"  # auto|opencli|getxapi|twitterapiio|official
export OPENCLI_BIN="/path/to/opencli"  # optional; defaults to opencli on PATH
export OPENCLI_MAX_WORKERS="1"  # optional; keep browser-backed OpenCLI serial by default
export OPENCLI_CLOSE_TABS_AFTER_RUN="1"  # optional; close OpenCLI-created X/Twitter tabs after fetch
export OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN="1"  # optional; close Chrome automation windows opened by OpenCLI
export GETX_API_KEY="..."        # GetXAPI fallback
export TWITTERAPI_IO_KEY="..."   # twitterapi.io fallback
export X_BEARER_TOKEN="..."      # Official X API v2 fallback
# Web Search
export TAVILY_API_KEY="tvly-xxx"   # Tavily Search API
export BRAVE_API_KEYS="k1,k2,k3"   # Brave Search API keys (comma-separated for rotation)
export BRAVE_API_KEY="..."         # Single Brave key
export WEB_SEARCH_BACKEND="auto"   # auto|brave|tavily
# GitHub
export GITHUB_TOKEN="..."          # GitHub API
# Other
export BRAVE_PLAN="free"           # Override Brave rate limit: free|pro
```

OpenCLI is preferred because it can reuse an authenticated Chrome/Chromium session instead of requiring Twitter API credentials. API backends remain available for CI, headless machines, or users who already configured API keys.

To use the OpenCLI backend, install the OpenCLI executable yourself and make it available on `PATH`, or set `OPENCLI_BIN` to its absolute path. In OpenClaw, also install the `jackwener/opencli` Skill so the agent can run `opencli doctor`, check the browser bridge, and guide X login-state troubleshooting.

OpenCLI browser bridge stability depends on the local browser extension connection. The fetcher defaults to serial OpenCLI requests (`OPENCLI_MAX_WORKERS=1`) to avoid tab-id churn during large KOL runs. It also closes X/Twitter tabs created during the OpenCLI fetch (`OPENCLI_CLOSE_TABS_AFTER_RUN=1` by default) and, on macOS, closes Chrome automation windows that OpenCLI opened during the run (`OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN=1` by default) while leaving pre-existing windows alone. Increase concurrency only after validating that your browser bridge remains stable.

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
pip install weasyprint
```

- **weasyprint** — Enables PDF report generation

## 📂 Repository

**GitHub**: [github.com/tangwz/follow-news](https://github.com/tangwz/follow-news)

## 🌟 Featured In

- [Awesome OpenClaw Use Cases](https://github.com/hesamsheikh/awesome-openclaw-usecases) — Community-curated collection of OpenClaw agent use cases

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.
