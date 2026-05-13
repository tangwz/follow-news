# Follow-News Config Editor (轻量网页版)

这个目录提供一个很轻量的 Web 编辑器，用来编辑默认的：

- `config/defaults/sources.json`
- `config/defaults/topics.json`

## 快速启动

```bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
python3 server.py
```

启动后访问：

- http://127.0.0.1:8787

## 功能

- 支持中/英文切换
- `sources.json`、`topics.json` 两栏独立编辑
- 支持两种模式：
  - 表格模式：快速修改常用字段（sources: id、name、type、url、enabled、priority、topics；topics: id、emoji、label、display.max_items、display.style、search.queries）
  - JSON 模式：直接编辑完整 JSON 文本（会做 JSON 合法性校验）
  - 搜索 + 分页（sources 更友好）

## 注意

- 本工具只会读写上述两份默认文件
- 保存前会校验 JSON 结构的顶层字段是否正确（`sources` 必须是数组，`topics` 必须是数组）
- 保存成功后会自动回写文件并刷新当前界面视图。

## 停止

在终端按 `Ctrl+C` 即可停止服务。
