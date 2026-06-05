# Novel Selector

一个命令行小说推荐原型：同步 Legado/“阅读”书源，广撒网探索明确完本小说，抓取前 10 章试读文本，再让 OpenAI 兼容接口的 LLM 按你的偏好推荐。

## 安装

```bash
uv sync --dev
```

## 配置

LLM 使用 OpenAI 兼容接口：

```bash
export OPENAI_API_KEY="..."
export OPENAI_BASE_URL="https://api.openai.com/v1"
export OPENAI_MODEL="gpt-4.1-mini"
```

可选配置：

```bash
export NOVEL_SELECTOR_DB="./data/novel-selector.sqlite3"
```

## 使用

```bash
uv run novel-selector init
uv run novel-selector sync-sources
uv run novel-selector discover --limit 100
# 调试或想控制探索方向时，可以手动指定一个或多个搜索种子
uv run novel-selector discover --limit 100 --seed 智斗 --seed 经营
uv run novel-selector sample --limit 30
uv run novel-selector recommend --k 5
uv run novel-selector feedback
```

## 设计要点

- 只推荐搜索或详情中明确标记为“完结 / 完本 / 已完成”的小说。
- `discover` 一旦探索到小说，就写入探索历史；后续探索会跳过相同“书名 + 作者”指纹，即使它之前没有进入推荐结果。
- 候选生成混合使用 LLM 偏好关键词、内置类型词、题材词、反向探索词、随机种子词，以及能兼容的发现页/榜单。
- 默认只缓存前 10 章试读内容，不做整本小说批量下载。
- Legado 规则第一版渐进兼容：支持常见 HTTP、JSONPath、CSS selector、XPath 和简单链式 HTML 规则；JS/WebView/Cookie 强依赖源会被标记为 unsupported 或 unstable。

## 测试

```bash
uv run pytest
```
