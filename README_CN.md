# Follow News

> 自动化科技资讯汇总 — 156 个内置数据源，6 层管道，一句话安装。

[English](README.md) | **中文**

[![Tests](https://github.com/tangwz/follow-news/actions/workflows/test.yml/badge.svg)](https://github.com/tangwz/follow-news/actions/workflows/test.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![ClawHub](https://img.shields.io/badge/ClawHub-follow--news-blueviolet)](https://clawhub.com/tangwz/follow-news)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## 💬 一句话安装

跟你的 [OpenClaw](https://openclaw.ai) AI 助手说：

> **"安装 follow-news，每天早上 9 点发送一份科技日报。"**

搞定。Bot 会自动安装、配置、定时、推送——全程对话完成。

更多示例：

> 🗣️ "配置一个每周 AI 周报，只要 LLM 和 AI Agent 板块，每周一发到 Discord #ai-weekly"

> 🗣️ "安装 follow-news，加上我的 RSS 源，发送科技新闻到 Telegram"

> 🗣️ "现在就给我生成一份科技日报，跳过 Twitter 数据源"

或通过 CLI 安装：
```bash
clawhub install follow-news
```

## 📊 你会得到什么

基于 **156 个内置数据源** + **5 个 Web 搜索主题** 的质量评分、去重科技日报：

| 层级 | 数量 | 内容 |
|------|------|------|
| 📡 RSS | 65 个订阅源 | OpenAI、Anthropic、Ben's Bites、HN、36氪、CoinDesk… |
| 🐦 Twitter/X | 60 个 KOL | @karpathy、@VitalikButerin、@sama、@elonmusk… |
| 🔍 Web 搜索 | 5 个主题 | Tavily 或 Brave Search API + 时效过滤 |
| 🐙 GitHub | 23 个仓库 | 关键项目的 Release 跟踪（LangChain、vLLM、DeepSeek、Llama…） |
| 🗣️ Reddit | 8 个子版块 | r/MachineLearning、r/LocalLLaMA、r/CryptoCurrency… |

### 数据管道

```
       run-pipeline.py (~30秒)
              ↓
  RSS ─┐
  Twitter ─┤
  Web ─────┤── 并行采集 ──→ merge-sources.py
  GitHub ──┤
  Reddit ──┘
              ↓
  质量评分 → 去重 → 主题分组
              ↓
    Discord / 邮件 / PDF 输出
```

**质量评分**：优先级源 (+3)、多源交叉验证 (+5)、时效性 (+2)、互动度 (+1~+5)、Reddit 热度加分 (+1/+3/+5)、已报道过 (-5)。

## ⚙️ 配置

- `config/defaults/sources.json` — 156 个内置数据源（65 RSS、60 Twitter、23 GitHub、8 Reddit）
- `config/defaults/topics.json` — 5 个主题，含搜索查询和 Twitter 查询
- 用户自定义配置放 `workspace/config/`，优先级更高

## 🎨 自定义数据源

开箱即用，内置 168 个数据源——但完全可自定义。将默认配置复制到 workspace 并覆盖：

```bash
# 复制并自定义
cp config/defaults/sources.json workspace/config/follow-news-sources.json
cp config/defaults/topics.json workspace/config/follow-news-topics.json
```

你的配置文件会与默认配置**合并**：
- **覆盖**：`id` 匹配的源会被你的版本替换
- **新增**：使用新的 `id` 即可添加自定义源
- **禁用**：对匹配的 `id` 设置 `"enabled": false`

```json
{
  "sources": [
    {"id": "my-blog", "type": "rss", "enabled": true, "url": "https://myblog.com/feed", "topics": ["llm"]},
    {"id": "openai-blog", "enabled": false}
  ]
}
```

不需要复制整个文件——只写你要改的部分。

## 🔧 环境变量

# Twitter/X 后端（自动优先级：opencli > getxapi > twitterapiio > official）
export TWITTER_API_BACKEND="auto"  # auto|opencli|getxapi|twitterapiio|official
export OPENCLI_BIN="/path/to/opencli"  # 可选；默认使用 PATH 上的 opencli
export OPENCLI_MAX_WORKERS="10"  # 可选；提高并发可加快抓取
export OPENCLI_CLOSE_TABS_AFTER_RUN="1"  # 可选；抓取后关闭 OpenCLI 新建的 X/Twitter 标签页
export OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN="1"  # 可选；关闭 OpenCLI 本次打开的 Chrome 自动化窗口
export GETX_API_KEY="..."        # GetXAPI fallback
export TWITTERAPI_IO_KEY="..."   # twitterapi.io fallback
export X_BEARER_TOKEN="..."      # Twitter/X 官方 API v2 fallback
# 网页搜索
export TAVILY_API_KEY="tvly-xxx"   # Tavily Search API
export BRAVE_API_KEYS="k1,k2,k3"   # Brave Search API 密钥（逗号分隔用于轮换）
export BRAVE_API_KEY="..."         # 单个密钥
export WEB_SEARCH_BACKEND="auto"   # auto|brave|tavily
# GitHub
export GITHUB_TOKEN="..."          # GitHub API
# 其他
export BRAVE_PLAN="free"           # 覆盖速率限制检测：free|pro

OpenCLI 是默认优先后端，因为它可以复用已经登录的 Chrome/Chromium 会话，不再强制要求 Twitter API 凭据。CI、无浏览器环境，或已经配置 API key 的用户仍可通过 API 后端 fallback。

如需使用 OpenCLI 后端，用户需要自行安装 OpenCLI 可执行文件，并确保它在 `PATH` 上，或通过 `OPENCLI_BIN` 指向其绝对路径。在 OpenClaw 中，还需要安装 `jackwener/opencli` Skill，这样 agent 才能运行 `opencli doctor`、检查浏览器桥接，并协助排查 X 登录态问题。

OpenCLI 的稳定性取决于本机浏览器扩展桥接状态。抓取器默认使用 10 并发 OpenCLI 请求（`OPENCLI_MAX_WORKERS=10`，上限 10），同时默认会关闭本次 OpenCLI 抓取中新建的 X/Twitter 标签页（`OPENCLI_CLOSE_TABS_AFTER_RUN=1`），并在 macOS 上关闭 OpenCLI 本次打开的 Chrome 自动化窗口（`OPENCLI_CLOSE_CHROME_WINDOWS_AFTER_RUN=1`），不会关闭执行前已经存在的窗口。

## 📦 依赖

### 核心依赖

本技能需要 Python 3.8+ 和两个可选依赖以增强功能：

```bash
pip install -r requirements.txt
# 或
pip install feedparser>=6.0.0 jsonschema>=4.0.0
```

- **feedparser** — RSS/Atom 订阅源解析（未安装时回退到正则匹配）
- **jsonschema** — 配置文件的 JSON Schema 验证

### 可选依赖

```bash
pip install weasyprint
```

- **weasyprint** — 启用 PDF 报告生成

## 🧪 测试

```bash
python -m unittest discover -s tests -v   # 41 个测试，纯标准库
```

## 📂 仓库地址

**GitHub**: [github.com/tangwz/follow-news](https://github.com/tangwz/follow-news)

## 🌟 相关引用

- [Awesome OpenClaw Use Cases](https://github.com/hesamsheikh/awesome-openclaw-usecases) — OpenClaw 社区精选用例合集

## 📄 开源协议

MIT License — 详见 [LICENSE](LICENSE)
