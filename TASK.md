# Novel Selector 后续开发任务

本文档用于在上下文窗口重开后快速恢复项目状态和开发方向。包含中文的文件按 UTF-8 阅读。

## 当前状态

- 项目是 Python CLI MVP，使用 `uv` 管理依赖。
- 本地配置通过 `.env` 读取，`.env` 已被 `.gitignore` 忽略。
- 产品核心是“本地喜欢小说冷启动画像 + 在线书源发现 + 试读推荐 + 反馈循环”。
- 首次使用前需要在与 `src/` 同级的 `novels/` 目录中放入至少 5 本用户喜欢的完本小说 `.txt` 文件。
- `init` 会读取 `novels/`，按章节边界切块，逐本交给 LLM 总结，再汇总生成 `initial_profile` 偏好画像。
- 单书总结按内容 hash 和上下文窗口缓存到 `local_novel_summaries`，内容未变化时可复用。
- 已初始化过偏好画像时，重复执行 `init` 会提示用户先执行 `clear`，不会重复消耗 LLM。
- 当前主流程：
  - `init`：初始化数据库，并根据本地喜欢小说构建初始偏好画像。
  - `clear`：删除本地 SQLite 数据库，重置到未初始化状态；不删除 `novels/`、`.env`、`logs/`。
  - `sync-sources`：同步 Legado/“阅读”书源 JSON。
  - `filter-sources`：测试书源搜索、元数据、完本判断、目录和正文能力，写入 `source_capabilities`。
  - `discover`：探索明确完本小说，并记录已探索指纹，后续不再重复探索；有书源过滤结果时优先使用通过过滤的源。
  - `sample`：抓取候选小说前若干章试读文本。
  - `recommend`：调用 OpenAI 兼容 LLM 阅读样本并推荐；允许返回少于 `k` 项或空推荐。
  - `feedback`：记录选择或跳过理由，并基于反馈更新新版偏好画像。
  - `status` / `show-profile` / `doctor`：诊断数据库、书源、候选、采样、推荐、反馈、画像和日志状态。
- 当前推荐池规则：
  - 已被推荐过的小说不再进入推荐池。
  - 在 `feedback` 阶段被明确不喜欢的小说，即 `feedback_events.selected = 0`，不再进入推荐池。
  - 未被推荐的采样候选仍可保留在推荐池。
  - LLM 不需要硬凑 `topk-1` 本偏好推荐；候选不合适时可返回 `[]`。
- 已验证：

```bash
uv run pytest  # 37 passed
uv run novel-selector --help
```

## 已完成重点任务

### 1. 完整日志系统

- 新增统一日志模块。
- 日志分为：
  - `workflow.log`：命令开始/结束、参数、统计信息、失败原因。
  - `source.log`：书源请求、解析结果、超时、403、规则不支持原因。
  - `llm.log`：模型、请求用途、prompt 摘要、原始响应、解析错误。
- 日志默认保存在 `logs/`，且 `logs/` 已加入 `.gitignore`。
- 支持按单文件大小轮转和按总日志目录大小清理最早日志。
- CLI 支持 `--log-level`，也支持 `NOVEL_SELECTOR_LOG_LEVEL`。
- 日志会脱敏 API key、authorization、token 等敏感字段。

### 2. 初始偏好画像冷启动

- 新增 `novels/` 目录作为用户喜欢的完本小说输入源。
- 除 `clear`、`status`、`show-profile`、`doctor`、`filter-sources` 和帮助命令外，CLI 执行前会检查 `novels/` 至少有 5 个非空 `.txt` 文件。
- 新增配置：
  - `NOVEL_SELECTOR_NOVELS_DIR=novels`
  - `NOVEL_SELECTOR_CONTEXT_WINDOW=1000000`
- `init` 构建初始画像流程：
  - 读取本地小说，支持 `utf-8-sig`、`utf-8`、`gb18030`。
  - 优先按章节标题切块，不为凑固定长度在章节中间切块。
  - 根据上下文窗口自动计算块大小，并预留 prompt、输出和安全余量。
  - 单章过长时按段落兜底切分，并写入日志。
  - 每本小说先生成单书摘要，再将所有单书摘要汇总为初始用户偏好画像。
  - 最终画像写入 `preference_events`，事件类型为 `initial_profile`。
- 单书摘要会按小说内容 hash 和上下文窗口保存到 `local_novel_summaries`，重新执行 `init` 时可复用未变化书籍的中间结果。

### 3. 重置命令

新增：

```bash
uv run novel-selector clear
uv run novel-selector clear --yes
```

- `clear` 删除当前配置指向的 SQLite 数据库及 WAL/SHM 文件。
- `clear` 不删除 `novels/`、`.env`、`logs/`。
- 默认需要用户确认；`--yes` 可跳过确认。

### 4. CLI 体验与诊断命令

- 裸命令会进入交互式 CLI：

```bash
uv run novel-selector
```

- 交互模式输入 `/` 会弹出命令列表，显示命令名和简短说明；支持键盘选择，终端支持时可用鼠标选择。
- 原脚本式命令继续保留，例如 `uv run novel-selector discover --limit 20 --seed 智斗`。
- 新增只读/诊断命令：
  - `status`：展示数据库、书源、候选、采样、推荐、反馈和画像状态。
  - `show-profile`：显示当前偏好画像；画像不存在时提示先执行 `init`。
  - `doctor`：检查 `.env`、LLM 配置、`novels/` 数量、数据库初始化、画像和日志目录。
- `status`、`show-profile`、`doctor` 会绕过 `novels/` 前置检查，便于未初始化时排查问题。

### 5. Legado 书源兼容性扩展

- 已支持常见静态 Legado 规则模式：`##` 替换/清理、`@put` / `@get` 变量传递、`{{...}}` 模板、POST 搜索请求元数据、书源 headers/cookies、分页 `nextTocUrl` 和分页 `nextContentUrl`。
- 已扩展搜索、书籍信息、目录和正文规则中的不支持书源识别，使依赖 JS/WebView/captcha 的书源会被跳过，而不是盲目重试。
- 已为变量/模板处理、替换规则和 POST 请求解析增加聚焦回归测试。

### 6. 书源过滤 CLI

- 已新增 `uv run novel-selector filter-sources`，支持 `--seed`、`--limit`、`--min-success-rate` 和 `--include-unstable`。
- 已新增 `source_capabilities` 持久化，用于记录搜索、元数据、完本检测、目录、正文、WebView/unstable 标记、成功率和主要错误类型。
- `discover` 现在会在存在过滤结果时使用已通过的书源能力，同时保留首次过滤前使用全部书源的旧行为。
- `status` 现在会报告书源过滤通过数和检查数。

### 7. 偏好画像迭代与推荐语义

- `feedback` 已从单纯追加事件升级为“事件追加 + 画像更新”。
- 每次保存反馈后会调用 LLM 读取旧画像和本轮反馈，生成新版完整中文偏好画像。
- 画像版本历史保留在 `preference_events`，不会覆盖旧记录。
- `discover` 的 LLM 搜索种子生成和 `recommend` 的评分 prompt 都读取最新版画像。
- `feedback` 支持选择多个推荐项；选中项和填写跳过理由的未选项都会写入反馈事件。
- 推荐 prompt 明确允许“不推荐任何一本”，候选不合适时 LLM 可返回空数组 `[]`。
- 推荐 prompt 不再强制 `topk-1` 本偏好小说，推荐数量可以少于 `k`。
- 被 feedback 阶段明确不喜欢的小说不再进入后续推荐池。

## 下一批重点任务

### 1. 建立数据库迁移机制

目标：避免后续 schema 变化只能依赖散落的 `_ensure_column`，方便长期迭代。

建议：
- 增加 `schema_migrations` 表。
- 将当前 `init()` 中的表结构创建和后续变更拆成版本化 migration。
- 写测试覆盖从空库、旧库升级到当前库。

### 2. 结构化反馈与画像

目标：让负反馈更可计算，后续能影响 seed 权重、采样优先级和推荐解释。

建议：
- 对跳过原因做结构化标签，例如：
  - 降智反派。
  - 题材太短。
  - 节奏水。
  - 后宫/套路爽文。
  - 文风不合。
- 让 LLM 输出结构化画像，例如强偏好、弱偏好、探索方向、明确避雷、题材权重。
- 保留当前中文完整画像作为给 LLM 的自然语言上下文，同时新增机器可读字段。

### 3. 区分评审结果和推荐曝光

目标：让 LLM 可以评审候选但不把低分候选记为正式推荐，避免“看过但不推荐”的语义混乱。

建议：
- 增加 rejected review 或 candidate_reviews 表。
- 记录低分候选的评审理由和风险，但不写入 `recommendation_items`。
- CLI 展示时区分“推荐结果”和“未推荐评审摘要”。

### 4. 改进候选生成和探索策略

目标：让发现阶段更稳定地产生高质量完本候选。

建议：
- 自动将长搜索词拆成短搜索词。
- 给 `discover` 增加搜索种子去重和冷却时间。
- 根据负反馈降低某类 seed 权重，例如“短篇”“现代日常纠纷”。
- 记录 seed 的发现成功率、采样成功率、推荐转化率。

### 5. 扩展 Legado/“阅读”书源支持

目标：继续提高真实书源的搜索、目录、正文抓取成功率。

建议：
- 参考开源软件“阅读”的源码实现规则解释器，因为本项目使用的就是阅读书源格式。
- 优先研究并兼容这些规则能力：
  - JSONPath 简写和插值。
  - 更复杂的 HTML CSS selector 链式规则。
  - 更复杂的 XPath。
  - 可静态求值的简单 JS。
- 对 JS/WebView 规则继续做分级：需要 WebView、登录、验证码、人机验证的源继续标记 unsupported。
- 为每类规则增加 fixture 测试，避免兼容一个源时破坏另一个源。

### 6. CLI 体验增强

目标：减少脚本参数记忆成本，让交互式使用更顺手。

建议：
- 为交互式 CLI 增加更细的参数表单，例如 `discover` 选择后逐项询问 `limit`、`seed`。
- 增加命令历史和最近一次执行结果摘要。
- 增加 `status --json`，方便脚本读取状态。

## 当前已知限制

- `init` 需要至少 5 本本地喜欢小说；数量不足时多数实际 CLI 命令会被阻止，只有 `clear`、`status`、`show-profile`、`doctor`、`filter-sources`、帮助命令可运行。
- 首次总结长篇小说可能耗时较长、消耗较多 token；内容未变化时单书摘要会复用缓存。
- 章节识别依赖常见章节标题格式；极端格式的 txt 可能退化为长章节兜底切分。
- 真实书源失败率仍然较高，常见失败包括 403、超时、目录为空、正文为空、规则暂不支持。
- 目前手动 `--seed` 仍然更容易稳定发现候选。
- 采样成功率依赖书源规则兼容度。
- 反馈理由尚未结构化，负反馈目前主要通过自然语言画像更新和“不再推荐该小说”生效。
