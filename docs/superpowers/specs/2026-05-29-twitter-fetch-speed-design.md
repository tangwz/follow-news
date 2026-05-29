# Twitter Fetch Speed Design

## 背景

`follow-news` 当前通过 `scripts/fetch-twitter.py` 拉取 Twitter/X KOL 时间线。默认配置中有 61 个启用的 Twitter source，其中 21 个是 priority source，40 个是 regular source。OpenCLI 是 `auto` 模式下的首选 backend，它复用本机 Chrome/Chromium 登录态，避免 Twitter API 凭据要求。

当前冷启动路径的主要耗时风险来自两类开销：

- OpenCLI 固定启动成本：版本检查、capability 检查、`doctor`、浏览器 tab/window snapshot 和 cleanup。
- 每个账号一次 OpenCLI 拉取：61 个 source 会放大进程启动、浏览器桥接和页面操作成本。

本设计聚焦冷启动全量拉取和 OpenCLI/浏览器开销，不改变下游合并、摘要、渲染或展示行为。

## 硬约束

- 不改变 `twitter.json` 的现有字段、字段含义、article/result shape。
- 不改变 digest、chat、discord、email、pdf 等任何显示内容格式。
- 不改变 Twitter metrics 当前的展示行为。
- 不改变 `merge-sources.py`、模板、golden 输出，除非测试用于证明优化没有影响显示格式。
- 新增 timing 或 diagnostics 只能进入日志或内部状态文件，不能进入会被下游渲染的 article 内容。
- 不默认丢弃 regular source，也不默认用 stale regular result 代替 fresh fetch。

## 目标

- 降低 OpenCLI 冷启动路径中的固定开销。
- 让 priority Twitter source 更早开始和更早完成。
- 保持全部 enabled Twitter source 的拉取语义。
- 保持现有 OpenCLI 错误分类和 API fallback 行为。
- 增加足够的 timing 观测，便于后续用数据判断瓶颈。

## 非目标

- 不实现 Twitter topic search。
- 不改变 API backend 的输出格式。
- 不重写 OpenCLI backend 为长期驻留服务。
- 不引入新的外部 Python 依赖。
- 不改变 digest 可见文本、排序模板、metrics 展示或 section 格式。

## 推荐方案

推荐实现 `OpenCliBackend` 内部的 fast path、分层调度和轻量 cleanup。改动范围限制在 `scripts/fetch-twitter.py` 及对应测试。

### OpenCLI Fast Path

OpenCLI 初始化拆成两层：

- 必须执行：解析 OpenCLI binary、最低版本门槛、第一次真实 fetch 的错误分类。
- 可缓存或条件执行：capability 检查、`doctor` 健康检查。

设计一个小的本地状态缓存，记录 OpenCLI binary identity、version、capability check 结果和 doctor check 结果。缓存只用于跳过重复检查，不用于跳过真实 source fetch。

建议缓存字段：

```json
{
  "opencli_path": "/usr/local/bin/opencli",
  "opencli_version": "1.7.22",
  "capability_checked_at": 1779984000,
  "doctor_checked_at": 1779984000,
  "doctor_status": "ok"
}
```

缓存失效条件：

- OpenCLI binary path 变化。
- OpenCLI version 变化。
- capability 或 doctor TTL 过期。
- 第一次真实 Twitter fetch 返回 browser/auth/capability 类全局错误。
- 用户显式启用 strict check。

`doctor` 不再每次冷启动都强制运行。缓存有效时先跳过；如果第一次真实 fetch 暴露 `opencli_browser_unavailable` 或 `opencli_auth_required`，再按现有错误分类返回或 fallback。

### 分层调度

保持完整 source 集合，但改变调度顺序：

1. 选择一个 priority source 作为 probe；如果没有 priority source，则使用第一个 enabled source。
2. probe 成功后，将剩余 sources 按 `priority=true` 在前、regular 在后的顺序入队。
3. 用现有 `ThreadPoolExecutor` 执行并发 fetch，但提交顺序优先 priority source。
4. 所有 result 都写入最终 `twitter.json`，不改变每个 result 的字段。

这个策略不保证输出 list 顺序与配置顺序一致，因为当前实现已经使用 `as_completed` 收集并发结果。它只保证 priority source 更早被提交，从而降低重点账号的等待时间。

如果后续需要稳定 result 顺序，应单独设计，并用 golden/merge 测试确认不会影响显示格式。本次不把稳定排序作为目标。

### 轻量 Cleanup

保留现有 cleanup 能力，但减少成功路径的固定成本：

- tab list 和 window snapshot 只在对应 cleanup 开关启用时执行。
- 成功路径避免重复执行昂贵 snapshot。
- 失败路径保留更强 cleanup，避免 Chrome automation window 或 X/Twitter tab 泄漏。

cleanup 只影响本地浏览器资源，不影响 `twitter.json` 内容。

### Timing 观测

为 OpenCLI backend 增加 phase timing 日志：

- `opencli.resolve`
- `opencli.version`
- `opencli.capability`
- `opencli.doctor`
- `opencli.browser_snapshot`
- `opencli.probe_fetch`
- `opencli.parallel_fetch`
- `opencli.cleanup`

source fetch 可以记录 handle、source id、status、elapsed milliseconds。日志只进入 stderr/log，不进入 article 或 digest。

## 数据流

```text
config/defaults/sources.json
  -> load_twitter_sources
  -> OpenCliBackend initialization
  -> cached capability/doctor fast path
  -> priority probe fetch
  -> prioritized concurrent fetch queue
  -> existing normalization
  -> unchanged twitter.json
  -> unchanged merge-sources.py
  -> unchanged digest rendering
```

## 错误处理

错误分类沿用现有语义：

- `opencli_missing`
- `opencli_capability_missing`
- `opencli_browser_unavailable`
- `opencli_auth_required`
- `opencli_timeout`
- `opencli_parse_error`
- `opencli_source_error`

fast path 不能吞掉 auth 或 browser bridge 问题。缓存只跳过预检查；真实 fetch 失败仍按现有全局错误或 per-source 错误处理。

在 `auto` backend 中，全局 OpenCLI 失败仍然 fallback 到 GetXAPI、twitterapi.io、official backend。显式 `opencli` backend 仍然不 fallback。

## 配置

初版尽量复用现有配置，只新增必要开关：

```text
OPENCLI_CHECK_CACHE_TTL_SECONDS=86400
OPENCLI_STRICT_CHECK=0
```

`OPENCLI_CHECK_CACHE_TTL_SECONDS` 控制 capability 和 doctor 预检查缓存 TTL。`OPENCLI_STRICT_CHECK=1` 强制每次运行都执行完整预检查，便于诊断。

不新增会影响显示输出的配置。

## 测试策略

单元测试不依赖真实 Twitter/X 或真实 OpenCLI。

需要新增或更新测试：

- capability/doctor 缓存命中时不重复执行对应 OpenCLI command。
- OpenCLI binary path 或 version 变化时缓存失效。
- 第一次真实 fetch 出现 auth/browser bridge 错误时不被缓存掩盖。
- priority source 先提交，最终 result 覆盖所有 source。
- timing 日志不会写入 article/result payload。
- normalized tweet article shape 与现有测试保持一致。

回归验证：

```bash
python3 -m unittest discover -s tests -v
```

显示格式保护：

- 现有 golden tests 必须保持不变。
- 不修改 `references/templates/`。
- 不修改 `scripts/merge-sources.py`。
- 不修改 digest rendering 逻辑。

## 验收标准

- `python3 -m unittest discover -s tests -v` 通过。
- OpenCLI backend 在缓存命中时减少 capability/doctor command 调用。
- priority source 在调度上先于 regular source。
- 所有 enabled Twitter source 仍会被尝试拉取，除非出现现有语义下的全局 backend failure。
- `twitter.json` shape 和下游显示内容格式不变。

## 后续可能演进

后续可以单独设计 deadline 模式，例如只要求 priority source fresh、regular source 允许复用短期缓存。但这会改变数据新鲜度语义，必须另起设计并显式标记 stale source，不属于本次范围。
