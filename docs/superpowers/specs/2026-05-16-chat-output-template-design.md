# Chat Output Template Design

## 目标

新增一个通用 IM 输出模板，用于 Telegram、飞书、微信、企业微信等聊天场景。模板关注可读性和转发稳定性，让每条新闻以“标题、内容总结、链接”的形式独立呈现，避免当前 Discord 列表式输出信息过薄的问题。

这个模板不追求单个平台的最佳渲染能力，而是使用各平台都能稳定展示的纯文本和轻量 Markdown。

## 范围

- 新增 `references/templates/chat.md`，作为 Telegram、飞书、微信等聊天工具的通用模板说明。
- 将 `<TEMPLATE>` 可选值扩展为包含 `chat`。
- 为确定性 acceptance renderer 增加 `chat` 输出模式和 golden 样例。
- 保留原始新闻标题和链接，不把标题改写成摘要。
- 为每条可见新闻增加一段更完整的中文内容总结。
- 链接统一使用 `🔗 URL`，不使用 `<URL>` 或 Markdown inline link。

## 非目标

- 不为 Telegram、飞书、微信分别拆出多个模板。
- 不修改抓取、去重、打分、排序、主题选择逻辑。
- 不引入新的 LLM enrichment pipeline。
- 不强制所有内容出现固定的 `理由：` 字段。
- 不要求 GitHub Releases 和 GitHub Trending 与普通新闻一样扩成长段总结。

## 输出结构

`chat` 模板的每条新闻使用三段结构：

```markdown
{{index}}. {{emoji}} [{{score}}/10] {{title}}

{{summary}}

🔗 {{link}}
```

规则如下：

- `index` 在每个 section 内从 1 开始。
- `emoji` 使用 topic emoji 或固定 section emoji。
- `score` 从 `quality_score` 映射到 10 分制，优先保持一位小数以内的紧凑表达。
- `title` 使用源数据原始标题，不做总结式改写。
- `summary` 是一段中文内容总结，通常 2-4 句。
- `summary` 可以包含价值判断或影响解释，但不固定使用 `理由：` 标签。
- `link` 使用源数据原始链接，单独成行。

## 内容总结规则

普通 topic 新闻的总结应说明三件事：发生了什么、核心信息是什么、为什么值得关注。重点是把标题背后的事实、观点或产品变化说清楚，而不是重复标题。

Twitter/X 或 KOL 内容应说明发言者是谁、提出了什么观点或动作、这个观点对技术趋势或行业判断有什么参考意义。互动数据只有在能解释传播强度或重要性时才写进总结。

RSS 和 Web 内容应优先包含产品、模型、版本、公司、人名、指标、发布时间等具体信息。只有 snippet 可用时，摘要应更谨慎；有 full text 或更丰富 evidence 时，可以写得更完整。

Reddit 内容应区分链接本身和社区讨论。可以提到 subreddit、分数、评论量或争论焦点，但不应把社区反应写成事实结论。

Podcast 内容只有在 transcript 可用时，才写 transcript-backed insight。缺少 transcript 时，只允许基于标题、节目名、snippet、时长和 source metadata 写元数据支持的总结。

GitHub Releases 和 GitHub Trending 保持短摘要风格，突出 repo、版本、语言、star 或核心变化，不强制扩展为长段观点。

## 模板关系

`discord.md` 继续服务 Discord 风格输出；`chat.md` 服务 IM 风格输出。两者可以共享基础字段和排序规则，但输出结构不同。

`chat` 是推荐给 Telegram、飞书、微信、企业微信的默认选择。后续如果某个平台确实需要专有能力，再单独新增平台模板。

## 数据流

现有 pipeline 继续生成 merged JSON。最终输出阶段根据 `<TEMPLATE>` 选择模板：

- `discord` 使用现有 Discord 结构。
- `email` 使用现有 HTML email 结构。
- `markdown` 保持现有兼容语义。
- `chat` 使用新的标题、总结、链接结构。

Acceptance renderer 需要支持渲染 `chat` 样例，便于测试输出结构是否稳定。

## 错误处理和降级

当新闻缺少可用 summary material 时，使用 title、snippet、summary、full_text 中可用的信息生成更短总结。不能凭空补充事实。

当链接缺失时，该条内容不应进入最终可见输出，因为 `chat` 模板把链接作为核心结构之一。

当分数缺失或非法时，使用现有质量分 fallback，并避免渲染异常。

当内容过长时，summary 应压缩为一个紧凑段落，避免 IM 群聊刷屏。

## 测试计划

- 新增或扩展 acceptance renderer 测试，覆盖 `chat` 输出模式。
- 增加 `tests/golden/daily-chat.md`，锁定标题、总结、链接三段结构。
- 验证链接行使用 `🔗 https://...`，不出现 `<https://...>`。
- 验证每个 topic section 至少有一条可见新闻。
- 验证 GitHub Releases 和 GitHub Trending 仍然保持短摘要，不被普通新闻规则强行扩写。
- 保留现有 Discord golden 测试，避免 `chat` 改动破坏 Discord 输出。

## 风险

更完整的中文总结会增加输出长度，在群聊里可能造成刷屏。模板需要限制每条 summary 为一个紧凑段落。

`chat` 总结质量仍然依赖最终生成阶段使用的 evidence。如果输入材料只有标题，输出必须短而谨慎。

如果把 `chat` 规则混入 `discord`，会让渠道语义变得不清晰。因此本设计选择新增模板，而不是改名或复用 Discord 模板。

## 验收标准

- 存在 `references/templates/chat.md`，明确描述通用 IM 输出格式。
- `<TEMPLATE>` 文档包含 `chat`。
- Acceptance renderer 可以生成 `chat` 样例。
- Golden 样例中的每条新闻包含标题行、内容总结和 `🔗 URL` 链接行。
- `chat` 输出不包含 `<URL>` 链接包裹格式。
- 现有 Discord 输出测试继续通过。
