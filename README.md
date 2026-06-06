# Novel Selector

一个命令行小说推荐原型：先用本地喜欢的完本小说构建初始偏好画像，再同步 Legado/“阅读”书源、发现完本候选、抓取试读样本，并用 OpenAI 兼容接口的 LLM 推荐。

## 安装

```bash
uv sync --dev
```

## 配置

LLM 使用 OpenAI 兼容接口：

```bash
OPENAI_API_KEY="..."
OPENAI_BASE_URL="https://api.deepseek.com"
OPENAI_MODEL="deepseek-v4-flash"
```

可选配置：

```bash
NOVEL_SELECTOR_DB="./data/novel-selector.sqlite3"
NOVEL_SELECTOR_TIMEOUT=5
NOVEL_SELECTOR_NOVELS_DIR=novels
NOVEL_SELECTOR_CONTEXT_WINDOW=1000000
```

## 初始画像

在首次执行 `init` 前，请在项目根目录的 `novels/` 中放入至少 5 本你喜欢的完本小说 `.txt` 文件。程序不会下载整本小说，只读取这些本地文件来生成初始偏好画像。

`init` 会按章节边界切块，每本小说先由 LLM 总结人物、情节、关键词、爽点、雷点、文风和节奏，并把单书总结保存到本地数据库；重新执行 `init` 时，内容和上下文窗口未变化的书会复用已有总结，再把所有单书总结合成为初始用户偏好画像。已初始化过时，`init` 不会重复生成画像。

## 使用

进入交互式 CLI：

```bash
uv run novel-selector
```

在交互模式中输入 `/` 可以弹出命令列表，使用上下键或鼠标选择命令。也可以继续使用脚本式命令：

```bash
uv run novel-selector init
uv run novel-selector sync-sources
uv run novel-selector discover --limit 100
uv run novel-selector discover --limit 100 --seed 智斗 --seed 经营
uv run novel-selector sample --limit 30
uv run novel-selector recommend --k 5
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

`clear` 不会删除 `novels/`、`.env` 或 `logs/`。

## 设计要点

- 至少 5 本本地喜欢小说用于冷启动画像，保证反馈循环有稳定初始点。
- `discover` 只收集搜索或详情中明确标记为“完结 / 完本 / 已完成”的小说。
- `discover` 一旦探索到小说，就写入探索历史；后续会跳过相同“书名 + 作者”指纹。
- 候选生成混合使用 LLM 偏好关键词、内置类型词、题材词、反向探索词和随机种子词。
- 默认只缓存前几章试读内容，不批量下载整本小说。
- Legado 规则第一版渐进兼容：支持常见 HTTP、JSONPath、CSS selector、XPath 和简单链式 HTML 规则；JS/WebView/验证码强依赖源会被标记为 unsupported 或 unstable。

## 测试

```bash
uv run pytest
```
