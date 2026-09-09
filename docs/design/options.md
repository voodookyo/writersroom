# 方案比较（阶段二）

- 日期：2026-08-26 ｜ 输入：`docs/research/` 阶段一结论

## 方案 A：纯 CLI + 文件系统证据仓（推荐）

- **用户流程**：`writersroom init` 建工作区 → `ingest` 导入文档 → `analyze` 出统计/信号/八维提示 → `version`/`compare` 管版本 → `meeting` 处理会议证据 → `stage` 跑故事开发阶段 → `import/kb/scenes/docgen` 通用工具。一切产物为工作区内 Markdown/JSON 文件，可用任意编辑器审阅，可用 git 管理。
- **最小可交付切片**：init → ingest(txt/md/fountain/docx/pdf) → analyze → compare → report.md。
- **采用/自研/延后**：采用见调研矩阵（pypdf/srt/webvtt-py/python-frontmatter 直接采用；screenplain/python-docx/jieba 适配封装）；自研：中文规范化、BM25、情绪词表、阶段运行器、全部领域逻辑；延后：任何 GUI、联网抓取、embedding 检索。
- **数据生命周期**：`sources/`（原始、不可变）→ `normalized/`（确定性规范化，可重建）→ `analysis/<版本>/<run_id>/`（每次运行新目录，不覆盖）→ `memory/candidates.jsonl`（候选）→ `memory/decisions.jsonl`（人工决定，append-only）→ `tasks/`（生成建议，不自动执行）。每条产物带来源哈希、工具版本、时间戳。
- **人工审核点**：候选记忆确认/否决/暂存；会议说话人更名与候选结论接受；阶段产物 confirm 后才成为 current；是否生成正文、采用哪稿、何时归档全部人工。
- **错误与恢复**：缺依赖/缺配置 → 明确错误+可操作提示（exit 2）；重跑写新 run 目录；`meeting restore`/`stage history` 可恢复人工审核历史。
- **威胁边界**：默认零网络；LLM 仅本地回环端点、环境变量配置、默认关闭；转写 PII 不出工作区；无遥测。
- **测试策略**：pytest 单元 + golden fixture + 端到端 smoke + 同输入双跑一致性检查；可观测性：每 run 的 manifest.json（输入哈希、参数、耗时、警告数）。
- **升级/迁移**：所有 JSON 带 schema 版本号，迁移器按版本号升级；维护成本：依赖 8 个以内运行时包。
- **失败退出路径**：每个外部依赖有适配层与回退（jieba→字符 n-gram；pypdf→pdfminer.six 备选；screenplain→自研简化 Fountain 解析可兜底）；仓库为纯文件，任何时候可用文件系统工具接管。

## 方案 B：方案 A + 本地 Web 审阅 UI（FastAPI）

- 同 A 的核心，增加 `writersroom serve` 本地 Web 界面：审阅候选记忆、会议候选结论、阶段产物对比。
- 代价：新增 FastAPI/uvicorn 依赖与前端静态资源；本地端口监听扩大威胁面（需绑定 127.0.0.1、随机端口、无鉴权面控制）；工期显著增加；UI 需独立测试策略。
- 结论：**延后**。核心全部文件化后，UI 是可叠加的薄壳，不影响 A 的先行交付。

## 方案 C：编排引擎（doit/Prefect）+ SQLite 索引

- 阶段编排交给外部引擎，检索/版本索引入 SQLite。
- 调研证据：doit 有 4 年发布空窗、状态存 `.doit.db`、DAG 引擎对线性阶段流程过重；SQLite 索引与「证据仓纯文件、可 diff、可 git」目标相抵；迁移与调试成本上升。
- 结论：**否决**。自研百行级阶段运行器 + 纯文件 manifest 已满足可重跑/可审计；doit 仅参考其 uptodate/file_dep 语义。

## 决策

选 **方案 A**。理由：本地优先与隐私边界最干净；确定性核心无任何外部模型依赖；证据分层与人工审核点天然落在文件系统上；维护成本最低；B 可在其后无损叠加。
