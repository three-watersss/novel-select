# Novel Selector 后续开发任务

本文档用于在上下文窗口重开后快速恢复项目状态和开发方向。

## 当前状态

- 项目是 Python CLI MVP，使用 `uv` 管理依赖。
- 本地配置通过 `.env` 读取，`.env` 已被 `.gitignore` 忽略。
- 当前主流程已跑通，但还属于原型：
  - `sync-sources`：同步 Legado/“阅读”书源 JSON。
  - `discover`：探索明确完本小说，并记录已探索指纹，后续不再重复探索。
  - `sample`：抓取候选小说前若干章试读文本。
  - `recommend`：调用 OpenAI 兼容 LLM 阅读样本并推荐。
  - `feedback`：记录选择或跳过理由，并写入偏好事件。
- 已验证命令：

```bash
uv run pytest
uv run novel-selector sync-sources
uv run novel-selector discover --limit 5 --source-limit 10 --max-searches 10 --seed 智斗
uv run novel-selector sample --limit 3 --chapters 10
uv run novel-selector recommend --k 2 --pool-size 10
uv run novel-selector feedback
```

## 重点任务

### 1. 构建完整日志系统

目标：能完整记录工作流关键信息和 LLM 对话，同时自动清理过旧日志，避免无限增长。

要求：
- 增加统一日志模块，不要在业务代码里散落 `print`。
- 日志至少分为：
  - workflow 日志：命令开始/结束、参数、统计信息、失败原因。
  - source 日志：书源请求、解析结果、超时、403、规则不支持原因。
  - llm 日志：模型、请求用途、prompt 摘要、原始响应、解析错误。
- 日志文件保存在本地忽略目录，例如 `logs/`。
- `logs/` 必须加入 `.gitignore`。
- 支持轮转或清理策略：
  - 按文件大小轮转，或
  - 按总日志目录大小上限删除最早日志。
- CLI 增加可选参数或环境变量控制日志级别。
- 避免在默认日志中泄露 API key。

### 2. 扩展 Legado/“阅读”书源支持

目标：显著提高真实书源的搜索、目录、正文抓取成功率。

建议：
- 参考开源软件“阅读”的源码实现规则解释器，因为本项目使用的就是阅读书源格式。
- 优先研究并兼容这些规则能力：
  - JSONPath 简写和插值。
  - HTML CSS selector 链式规则。
  - XPath。
  - `##` 替换/过滤语法。
  - `@put` / `@get` 变量传递。
  - `{{...}}` 模板表达式。
  - `nextTocUrl` 和分页目录。
  - `nextContentUrl` 和分页正文。
  - POST 搜索请求。
  - 常见 header/cookie 配置，但不要绕过验证码或访问控制。
- 对 JS/WebView 规则先做分级：
  - 可静态求值的简单 JS 可考虑支持。
  - 需要 WebView、登录、验证码、人机验证的源继续标记 unsupported。
- 为每类规则增加 fixture 测试，避免兼容一个源时破坏另一个源。

### 3. 新增书源过滤 CLI

目标：建立符合本产品要求的书源白名单，减少 discover 阶段无效请求。

新增命令建议：

```bash
uv run novel-selector filter-sources
```

建议功能：
- 扫描已同步书源，按产品要求做能力测试。
- 只保留或标记满足以下条件的书源：
  - 支持搜索。
  - 能返回书名、作者、简介或标签。
  - 能明确判断完本状态。
  - 能获取目录。
  - 能抓取至少第一章正文。
  - 不依赖验证码、强登录或 WebView。
- 将测试结果写入数据库，例如 `source_health` 或新增 `source_capabilities` 表。
- `discover` 默认只使用通过过滤的书源。
- 支持参数：
  - `--seed`：用指定搜索词测试。
  - `--limit`：限制测试源数量。
  - `--min-success-rate`：最低成功率。
  - `--include-unstable`：是否包含不稳定源。
- 输出摘要：
  - 可用源数量。
  - 搜索可用但不可采样源数量。
  - 完本状态不可判断源数量。
  - 失败最多的错误类型。

## 其他建议任务

- 改进候选生成：
  - 自动将长搜索词拆成短搜索词。
  - 给 `discover` 增加搜索种子去重和冷却时间。
  - 根据负反馈降低某类 seed 权重，例如“短篇”“现代日常纠纷”。
- 改进推荐质量：
  - 在 LLM prompt 中明确允许“不推荐任何一本”。
  - 区分“推荐结果”和“低分评审结果”。
  - 对低分候选不要写入正向曝光推荐，或单独记录为 rejected review。
- 改进反馈：
  - 支持选择多个书。
  - 支持对跳过原因做结构化标签。
  - 反馈后让 LLM 更新一份结构化偏好画像，而不是只追加事件。
- 改进数据库：
  - 增加迁移机制。
  - 增加 source capability 表。
  - 增加 sample 失败原因表。
- 改进 CLI 体验：
  - 增加 `status` 命令，展示数据库统计、可用源数量、待采样候选数量。
  - 增加 `show-profile` 命令，查看当前偏好画像。
  - 增加 `reset-dev-data` 命令，用于清理本地测试数据。

## 当前已知限制

- 真实书源失败率较高，常见失败包括 403、超时、目录为空、正文为空、规则暂不支持。
- 目前需要手动 `--seed` 才更容易稳定发现候选。
- 采样成功率依赖书源规则兼容度。
- 当前 LLM 推荐可能返回低分“不推荐”，CLI 已能提示 LLM 没返回推荐，但推荐语义仍需继续细化。

