# Novel Selector

一个 CLI-first 的网络小说推荐原型：用本地喜欢的完本小说冷启动偏好画像，再同步 Legado/“阅读”书源，发现完本候选，抓取前几章试读样本，并交给 OpenAI 兼容 LLM 做推荐。用户反馈会继续更新画像，明确不喜欢的小说不会再次进入推荐池。

## 安装

```bash
uv sync --dev
```

## 配置

LLM 使用 OpenAI 兼容接口。项目会读取根目录 `.env`：

```bash
OPENAI_API_KEY="..."
OPENAI_BASE_URL="https://api.deepseek.com"
OPENAI_MODEL="deepseek-v4-flash"
```

可选配置：

```bash
NOVEL_SELECTOR_DB="./data/novel-selector.sqlite3"
NOVEL_SELECTOR_SOURCE_URL="https://legado.aoaostar.com/sources/b778fe6b.json"
NOVEL_SELECTOR_TIMEOUT=8
NOVEL_SELECTOR_NOVELS_DIR=novels
NOVEL_SELECTOR_CONTEXT_WINDOW=1000000
NOVEL_SELECTOR_LOG_DIR=logs
NOVEL_SELECTOR_LOG_LEVEL=INFO
```

## 初始画像

首次执行 `init` 前，请在项目根目录的 `novels/` 中放入至少 5 本你喜欢的完本小说 `.txt` 文件。程序不会下载整本小说，只读取这些本地文件来生成初始偏好画像。

`init` 会按章节边界切块，每本小说先由 LLM 总结人物、情节、关键词、爽点、雷点、文风和节奏，再把所有单书总结合成为 `initial_profile`。单书总结会按内容 hash 和上下文窗口缓存到本地数据库；重新执行时，内容未变化的书会复用缓存。已初始化过时，`init` 不会重复生成画像，需要先执行 `clear`。

本地小说读取支持 `utf-8-sig`、`utf-8`、`gb18030`。包含中文的文件建议使用 UTF-8。

## 使用

进入交互式 CLI：

```bash
uv run novel-selector
```

交互模式中输入 `/` 可以弹出命令列表，支持键盘选择，终端支持时也可用鼠标选择。也可以使用脚本式命令：

```bash
uv run novel-selector init
uv run novel-selector sync-sources
uv run novel-selector filter-sources --seed 修仙 --limit 50
uv run novel-selector discover --limit 100
uv run novel-selector discover --limit 100 --seed 智斗 --seed 经营
uv run novel-selector sample --limit 30 --chapters 10
uv run novel-selector recommend --k 5 --pool-size 30
uv run novel-selector feedback
uv run novel-selector status
uv run novel-selector show-profile
uv run novel-selector doctor
```

重置本地数据库和偏好画像：

```bash
uv run novel-selector clear
uv run novel-selector clear --yes
```

`clear` 会删除当前配置指向的 SQLite 数据库及 WAL/SHM 文件，但不会删除 `novels/`、`.env` 或 `logs/`。

## 推荐与反馈规则

- `recommend` 只从已采样且未被推荐过的小说中取候选。
- 如果某本小说在 `feedback` 阶段被明确跳过并填写理由，即记录为负向反馈，后续不会再次进入推荐池。
- LLM 推荐允许返回少于 `--k` 项；如果候选池没有符合喜好的小说，也允许返回空数组，不会强行凑满推荐。
- 当 LLM 未返回推荐时，CLI 会提示“已有样本但模型没有返回推荐”。
- `feedback` 支持选择多个推荐项；对选中或填写了跳过理由的项目写入反馈事件。
- 有反馈保存后，会调用 LLM 基于旧画像和本轮反馈生成新版偏好画像，并保留在 `preference_events` 历史中。

## 设计要点

- 至少 5 本本地喜欢小说用于冷启动画像，保证反馈循环有稳定初始点。
- `discover` 只收集搜索或详情中明确标记为“完结 / 完本 / 已完成”的小说；未知状态不进入候选。
- `discover` 一旦探索到小说，就写入探索历史；后续会跳过相同“书名 + 作者”指纹。
- 候选生成混合使用 LLM 偏好关键词、内置类型词、题材词、反向探索词和随机种子词。
- `filter-sources` 会测试书源的搜索、元数据、完本判断、目录和正文能力；已有过滤结果时，`discover` 优先使用通过测试的书源。
- 默认只缓存前几章试读内容，不批量下载整本小说。
- Legado 规则第一版渐进兼容：支持常见 HTTP、JSONPath、CSS selector、XPath、`##` 替换、`@put` / `@get`、`{{...}}` 模板、分页目录/正文和 POST 搜索；JS/WebView/验证码强依赖源会被标记为 unsupported 或 unstable。
- 日志分为 `workflow.log`、`source.log`、`llm.log`，默认保存到 `logs/`，会脱敏 API key、authorization、token 等敏感字段。

## 测试

```bash
uv run pytest
```
