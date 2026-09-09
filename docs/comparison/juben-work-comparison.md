# 黑盒对比报告：writersroom vs juben-work-sanitized

- 日期：2026-08-26
- 对比对象：私有仓库 `juben-work-sanitized`（作者本人 GitHub，克隆至临时目录 `/tmp/juben-work-sanitized`）
- 对比基准：对方 `docs/COMPARISON_SCORECARD.md` 空白模板（0–3 分制：0=缺失，1=部分/仅协议，2=可用但有明显限制，3=测试验证且边界清晰）
- 方法：黑盒。不读对方源码设计文档以外的内部实现思路；所有行为结论来自隔离环境实测（临时虚拟环境 `/tmp/jw-venv`，产物在 `/tmp/jw-out`、`/tmp/jw-vault`），我方结论来自本仓库测试与实测。

## 0. 口径说明（先说清，避免数字游戏）

对方评分卡模板的分区标注为 A(30)+B(18)+C(24)+D(18)=90，但模板实际行数为 12+8+8+6=34 行，按 0–3 分制满分为 102。即**模板自身的分区标注与行数不符**（模板缺陷，记入 D6 证据）。本报告按 34 行逐行打分，并给出三种总计口径：

- **逐行满分口径**：满分 102。
- **核心需求口径**：剔除原需求未要求、属对方扩展的 4 行（A8/A9/A10 语音链路、C6 时间范围排除段）后 30 行，满分 90。这是与原始任务提示词对齐的口径。
- 对方模板标注口径（90）与核心需求口径恰好同值，但含义不同，不混用。

## 1. 逐项评分

### A. 确定性功能（12 行，行满分 36）

| 维度 | juben-work | writersroom | 证据（前者 / 后者） |
|---|---:|---:|---|
| 项目/版本 CRUD 与正文摘要 | 2 | 3 | 实测 CRUD 可用但小样例行会污染 synopsis 字段 / `tests/test_project.py`、`test_cli.py` 全链 |
| 文本/DOCX/PDF 输入与警告 | 2 | 3 | 输入能力分散在多个工具，无统一编码/解析/扫描件警告层；DOCX 读取含页眉页脚 XML（比我方全）/ `src/writersroom/ingest.py`，`tests/test_ingest.py`（编码、解析、扫描件警告） |
| 统计、角色、关键词、类型信号 | 2 | 3 | mini.fountain 实测：场次 5、集数 2 正确，但 dialogue_count=0、characters=[]（只认冒号式对白），risks 中如实披露漏检 / `tests/test_analyze.py`，同样例全识别 |
| 八维评估、风险和优先级 | 2 | 3 | 实际输出 9 维（多"剧集连续推进"）与文档宣称 8 维漂移；维度无证据定位（无行号/场次引用）；实质改写仅单维 +0.3，评分钝感 / `src/writersroom/hints.py` 八维各带证据/理由/风险/优先级/建议，`tests/test_hints.py` |
| 版本比较与工作流任务卡 | 3 | 2 | dimension_delta/stat_delta/关键词变化/improvements/regressions/persistent 齐全，9 张任务卡含 codex_prompt/priority_score/human_decision/can_replace / 比较五分类齐全（`tests/test_compare.py`），但 tasks 只是建议清单，无任务卡结构 |
| 会议命令/配置/文件发现 | 3 | 3 | meeting-assistant `pytest -q` 203 passed，按日期/项目/类型发现输入 / `tests/test_meetings.py` 21 项（locate/ingest/normalize/compact/review/restore） |
| SRT/文本/DOCX 规范化与 manifest | 3 | 3 | 规范化保留时间/原文/说话人/置信，manifest 版本化 / 同左，`test_meetings.py` |
| ASR 结果合并、CER、truth/review | 3* | 0 | 确定性融合与 CER 有 203 测试背书；*完整模型链路（FunASR/Qwen3-ASR）依赖重模型与 HF token，本次未实测 / 无此功能（需求外，见 §4 新增发现） |
| diarization 对齐和全局偏移 | 2 | 0 | 代码存在，重依赖 lazy 加载，未实测 / 无此功能（需求外） |
| 声纹候选、阈值和审核导出 | 2 | 0 | 代码存在（CAM++、审核导出），建议式不自动命名；未实测 / 无此功能（需求外） |
| 紧凑 AI 交接、知识包、风险队列 | 3 | 2 | 交接/知识包/风险队列实测生成，保留"未标注发言" / 紧凑交接带时间戳与 UNKNOWN/[?] 标记（`test_meetings.py`），但无知识包/风险队列概念 |
| 项目导入、KB 检索、场次索引、DOCX | 2 | 3 | 四工具均能跑，但：导入只处理 *.md（6 个 .txt 被静默忽略且报告 skipped=0）、无 dry-run、用 move 非 copy；KB 输出路径硬编码 `00_知识库/` 前缀、行内 `tags: [a,b]` 不解析；场次索引对非自家格式输出空索引且无警告 / `tests/test_importer.py`（dry-run、不覆盖、报告）、`test_kb.py`、`test_scenes.py`、`test_docgen.py` |
| **小计** | **29** | **25** | |

### B. 编剧协议（8 行，行满分 24）

| 维度 | juben-work | writersroom | 证据 |
|---|---:|---:|---|
| 故事前提→结构→人物→文本顺序 | 2 | 3 | 协议层（CLAUDE.md/工作流文档规定顺序），无代码强制，依赖 Agent 自律 / `stages.py` 写前锁定八项为代码闸门，未锁定无法进正文类阶段，`tests/test_stages.py` 验证 |
| 人类最终决策与确认门 | 3 | 3 | 任务卡 human_decision 字段、工作流确认门 / stage confirm/lock 由人触发，tasks 不自动执行创作决定，`test_stages.py` |
| Agent/角色卡职责边界 | 3 | 2 | 11 个 Agent 定义文件职责清晰 / 无独立 Agent 文件，角色指导内嵌于 41 个阶段模板（有意差异，见 §3） |
| Skill/Command/Workflow 路由 | 3 | 2 | 14 commands / 39 skill / 13 workflows 路由存在且文档化（作者自承"文件存在≠自动并行"）/ CLI 13 命令 + 4 条管线模板路由，无 skill/workflow 概念（有意差异） |
| 研究、知识库、版本和决策持久化 | 3 | 3 | 30 篇知识库、版本化、会议决策记录 / kb 检索、多标签版本、memory 候选→决定五态（确认/提案/否决/暂存/待核），`test_memory.py` |
| 审读→修改→质检闭环 | 2 | 2 | 工作流文档含审读/质检环节，协议层 / create 管线含审读/连续性/去模板化阶段模板，协议层，双方均无确定性审读工具 |
| 网文/短剧格式路由 | 2 | 2 | 11 个网文 SKILL.md 协议完整；Node CDP 爬虫代码存在但未实测，且外部页面属不稳定依赖（已声明）/ webnovel 管线含素材分析/平台适配/质量审阅阶段模板（管线结构有测试），无外部采集工具（有意） |
| Codex 任务卡和交付记录 | 3 | 2 | 9 张任务卡字段完整（codex_prompt/priority_score/can_replace/replacement_boundary）/ tasks 仅生成建议清单（见 §4 候选改进） |
| **小计** | **21** | **19** | |

### C. 证据与隐私（8 行，行满分 24）

| 维度 | juben-work | writersroom | 证据 |
|---|---:|---:|---|
| 原始证据不可被派生物覆盖 | 3 | 3 | 原子替换 + `.history.csv` / runs/ 每次运行新目录不覆盖 + meeting restore，`test_meetings.py` 历史恢复用例 |
| 人工修订高于 ASR/声纹/摘要 | 3 | 3 | review 覆盖链路有测试 / review-rename/review-candidate 人工覆盖 + restore 恢复，测试验证 |
| 未知/混合说话人不强行命名 | 3 | 3 | 保留"未标注发言" / 保留 `UNKNOWN` 与 `[?]` 不确定标记，测试验证 |
| 审核状态幂等、原子、可追溯 | 3 | 3 | 幂等审核 + 原子写 + 历史表 / 运行目录不可变 + 恢复日志 |
| 长文本使用紧凑时间轴 | 3 | 3 | 紧凑交接实测生成 / 交接带时间戳，测试验证 |
| 时间范围不重叠且排除段可解释 | 3 | 0 | knowledge-first 特性，有测试 / 无此概念（差异记录 §3，列候选改进） |
| 无真实项目/人名/路径/密钥/媒体 | 3 | 3 | `audit_sanitized_repo.py` EXIT=0 / `scripts/audit.py` 通过，CI 执行 |
| 红果分析和财务能力确实排除 | 3 | 3 | 文档明确排除，仓库检索无相关实现 / 全仓检索（排除 .venv 第三方包）无相关代码或文档声称 |
| **小计** | **24** | **21** | |

### D. 工程质量（6 行，行满分 18）

| 维度 | juben-work | writersroom | 证据 |
|---|---:|---:|---|
| Python/脚本语法和静态检查 | 3 | 3 | `verify_repository.py` EXIT=0（167 个 py 语法通过；资源计数为硬编码断言）/ `scripts/audit.py` + 测试套件 |
| 单元测试覆盖确定性核心 | 3 | 3 | 203 passed（meeting-assistant，仅依赖 PyYAML）/ 153 passed |
| 合成 smoke test 可重复 | 2 | 3 | 测试自含样例，但无端到端双跑一致性验证；小样例行污染 synopsis / `scripts/smoke.sh` 端到端双跑产物一致，`examples/` 最小样例 |
| 缺失外部依赖时错误清晰 | 2 | 3 | 重模型 lazy 加载、声明清晰；但实测出现静默行为反例：.txt 导入静默忽略、非预期格式空索引无警告 / 确定性能力不可用或缺模型时显式报错（`llm.py` 适配层、ingest 警告），测试覆盖 |
| macOS/Windows 配置可移植 | 2 | 2 | 有 Windows ps1 链 + macOS sh，但 7 个环境变量、重模型依赖、无锁文件 / 纯标准库跨平台（可选依赖隔离），但仅在 macOS 实测，Windows/Linux 未验证 |
| 文档与实际目录/接口一致 | 1 | 3 | 八维/九维漂移、README 计数漂移（作者自承并修正过）、评分卡模板分区标注与行数不符、两处代码副本漂移（kb_search.py 差 1 行、import_project.py 差 11 行）/ `docs/capabilities.md` 诚实分级（已运行验证/测试验证/代码存在/外部依赖/协议约定/未完成），与实测逐条核对 |
| **小计** | **13** | **17** | |

### 总分

| 口径 | juben-work | writersroom |
|---|---:|---:|
| 逐行满分（102） | **87** | **82** |
| 核心需求口径（剔除需求外 4 行，满分 90） | **77** | **82** |

解读：逐行口径下对方领先 5 分，全部来自原需求未要求的语音链路（ASR/分离/声纹，其中模型部分未实测）和时间范围排除段特性。在原始任务提示词定义的核心需求范围内，writersroom 领先 5 分，优势集中在确定性工具的证据定位、静默失败防护、文档一致性和端到端可复现性上。

## 2. 同样例行为对比（实测）

同一份 `examples/mini.fountain`（2 场、林晓/周岚对白）：

| 观察点 | juben-work | writersroom |
|---|---|---|
| 场次/集数统计 | 正确（5 场景标题计数口径下场次 5、集数 2） | 正确（2 场，按 fountain 场景标题） |
| 对白/角色提取 | dialogue_count=0、characters=[]（只认冒号式）；risks 如实披露"尚未识别到稳定的角色对话标记"——**披露式降级，非伪造成功** | 场次、角色频次、对白数全识别 |
| 场次索引导航 | 对该格式输出"场次数：0"空索引且**无警告**（其 CURRENT_LIMITS 已声明格式依赖，但运行时不提示）；转换为自家 `**场号 标题**` 格式后正常 | 提取场号/标题/源位置/对白角色/首尾摘录，格式不识别时给警告 |
| 版本比较敏感度 | 实质改写仅单维 +0.3，其余维度不动 | 统计/角色/关键词/维度四类差分 + 新增/减少/改善/退步/持续五分类 |

导入对照：对方 6 个 `.txt` 被静默忽略且报告 skipped=0、无 dry-run、shutil.move 移动源文件；writersroom 为 dry-run 默认、复制非移动、不覆盖保护、导入报告列明跳过原因。

DOCX 生成对照：双方产物均通过独立复核（对方 39872 字节、内联 audit 断言页面/边距/行距；我方 build+verify 结构断言）。对方需系统安装 Noto Sans CJK SC；我方页眉页脚读取不如对方（对方读段落+页眉页脚 XML，我方只读段落——记为我方待办）。

## 3. E. 差异记录

| 主题 | juben-work 行为 | writersroom 行为 | 有意？ | 影响/回归测试 |
|---|---|---|---|---|
| 协作编排 | 11 Agent + 14 command + 39 skill + 13 workflow 的 Agent 编排 | 4 条管线 41 阶段模板 + 人控确认门 | **有意** | 原提示词明确"不预设必须采用多 Agent"；writersroom 选择管道+模板。test_stages.py |
| 写前锁定 | 协议层顺序约束 | 代码强制八项锁定闸门 | **有意**（架构选择） | 我方闸门可测试验证；对方依赖 Agent 自律 |
| 任务卡 | codex_prompt/priority_score/can_replace 结构化任务卡 | 建议清单 | 我方**待改进**（候选，非缺陷） | 见 §4 |
| ASR/说话人分离/声纹 + 审核 WebUI | 有（重模型外部依赖） | 无 | 需求外新增发现 → **延后** | 见 §4 |
| 时间范围排除段可解释 | 有（knowledge-first） | 无 | 新增发现 → **候选改进** | 见 §4 |
| 导入安全默认 | move 源文件、无 dry-run、.txt 静默忽略 | copy、默认 dry-run、跳过必报告 | 我方有意；对方 .txt 静默为**缺陷** | test_importer.py |
| 格式不识别 | 空索引无警告（静默） | 显式警告/报错 | 原提示词要求"明确、可操作的错误，而不是伪造成功" | test_scenes.py / test_ingest.py |
| 评估证据定位 | 维度无行号/场次引用 | 每项提示带证据定位 | 我方改进 | test_hints.py |
| 评估维度数 | 实际 9 维 vs 文档 8 维 | 8 维与规格一致 | 对方**文档缺陷** | — |
| Windows 支持 | ps1 安装链 | 未验证 Windows | 我方**待办** | — |
| DOCX 读取范围 | 段落+页眉页脚 XML | 仅段落 | 我方**待办** | — |
| Agent git 权限 | settings.json 授予 Agent git add/commit | 无此授权 | **有意不采纳**（供应链/权限风险） | — |
| 代码副本 | kb_search.py / import_project.py 两处副本漂移 | 单一实现 | 对方**缺陷**（作者已自承） | — |

## 4. 新增发现与处置（对方有、原需求未写）

按原任务规则：先列影响，再决定是否纳入。

1. **ASR 融合 / 说话人分离 / 声纹命名建议 + 审核 WebUI**（meeting-assistant 重模型链路）。
   影响：引入 FunASR/Qwen3-ASR/Pyannote/CAM++/mlx_whisper/FFmpeg 等重依赖与 HF token 管理，与"默认离线、零外部依赖"原则直接冲突；确定性部分（融合/CER/审核状态机）质量高（203 测试）。
   处置：**延后**。若未来纳入，只能以可选 extras 形态，且确定性融合层与模型层必须保持当前对方的 lazy 隔离方式。
2. **6 个本地审核 WebUI**。影响：等同原设计阶段已识别并推迟的"方案 B（本地 UI）"。处置：**维持延后**，不新增。
3. **Codex 任务卡**（codex_prompt/priority_score/human_decision/can_replace/replacement_boundary）。影响：让"下一步任务"可直接交接给编码 Agent 执行。处置：**候选改进**——writersroom 的 tasks 输出可增加结构化 prompt 与优先级字段，但不照搬其看板形式。
4. **时间范围不重叠且排除段可解释**（knowledge-first handoff）。影响：长转写交接的覆盖率可审计。处置：**候选改进**——我方 handoff 可增加 coverage/排除段报告。
5. **hooks / Obsidian 同步 / Node CDP 爬虫 / 规则式聊天**。影响：不稳定外部依赖、宿主绑定或伪智能风险。处置：**不采纳**。

## 5. 总结

- **总分**：逐行口径 juben-work 87 / writersroom 82（满分 102）；核心需求口径 juben-work 77 / writersroom 82（满分 90）。
- **writersroom 明显更好之处**：每个确定性工具的证据定位与静默失败防护；写前锁定的代码强制；端到端 smoke 双跑一致；导入安全默认（dry-run/copy/不覆盖）；文档与实现一致性（capabilities.md 分级经逐条核对）；评估八维带证据/理由/风险/优先级/建议。
- **juben-work 仍更稳之处**：会议证据链路深（203 测试；ASR 融合/CER/审核状态机/声纹建议均为工程实现而非文档）；任务卡结构化程度高；审核 WebUI 可用；Windows 安装链存在；DOCX 读取覆盖页眉页脚；handoff 的时间范围排除段可解释。
- **未完成或需人工验收之处**：对方重模型链路（FunASR/Qwen3-ASR/Pyannote/CAM++）本次未实测，其"可用"结论仅覆盖确定性部分；对方网文爬虫未实测；writersroom 未在 Windows/Linux 验证。
- **不应由自动评分决定的创作质量问题**：八维/九维评估的维度措辞与建议质量、任务卡 prompt 的写作水准、模板对编剧实践的指导性——这些是文本质量判断，双方均应由人类主创评审，不纳入计分。

## 6. 证据附录

- 对方实测（隔离环境，命令与输出已记录）：`audit_sanitized_repo.py .` / `verify_repository.py .` EXIT=0；meeting-assistant `pytest -q` → 203 passed；tools 四件套（import/kb/scenes/docgen）在 `/tmp/jw-vault`、`/tmp/jw-out` 的运行产物；screenwriter engine 无头调用 `analyze_script()` 对 mini.fountain 及中文样例的输出。
- 我方验证：`python -m pytest tests/ -q` → 153 passed；`scripts/smoke.sh` 端到端双跑一致；`python scripts/audit.py` 通过。
- 对方仓库与产物位于临时目录（`/tmp/juben-work-sanitized`、`/tmp/jw-venv`、`/tmp/jw-out`、`/tmp/jw-vault`），确认后可整体删除，不留依赖。
