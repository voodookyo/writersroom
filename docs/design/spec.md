# 需求规格（可执行）—— writersroom v0.1

- 日期：2026-08-26 ｜ 上游：`docs/research/`、`docs/design/adr-001.md`、`docs/design/threat-model.md`
- 本文件是实现的契约：目录结构、JSON schema、CLI 行为、验收标准以此为准。

## 1. 工作区布局

```
<ws>/                            # 一个 writersroom 工作区 = 一个项目
  project.json                   # 项目档案（schema 1）
  versions.json                  # 版本标签表（schema 1）
  sources/<source_id>/
    original.<ext>               # 原始文件，导入后只读
    provenance.json              # sha256/相对路径/导入时间/工具版本/格式
  normalized/<source_id>.json    # 文档模型（schema 1，可删可重建）
  analysis/<version_label>/<run_id>/
    stats.json signals.json hints.json report.md manifest.json
  memory/candidates.jsonl        # 候选记忆（append-only）
  memory/decisions.jsonl         # 人工决定（append-only）
  tasks/tasks.json tasks.md      # 生成的下一步任务（建议，不执行）
  meetings/<meeting_id>/         # meeting_id = <YYYY-MM-DD>_<类型>
    raw/                         # 原始转写/笔记 + provenance.json
    normalized.json              # 规范化 turns（schema 1）
    runs/<run_id>/               # 自动结果（分段、候选结论），不覆盖
    review/overrides.json        # 人工覆盖（说话人更名、候选接受/否决）
    review/history.jsonl         # 审核历史（append-only，可恢复）
    handoff.md                   # 紧凑交接（带时间戳）
  knowledge/*.md                 # 知识库（frontmatter + 标题 + 标签 + 正文）
  stages/<pipeline>/<stage_id>/
    runs/<run_id>/{output.md, manifest.json}
    current.md                   # 人工 confirm 后才存在
    history.jsonl                # run/confirm 历史
  imports/<run_id>/{plan.json, report.md}
  exports/*.docx                 # docgen 输出
```

`run_id` = UTC 时间戳 `%Y%m%dT%H%M%SZ`（同秒冲突追加 `-2`、`-3`）。所有 JSON 含 `schema` 版本号；所有自动产物含 `produced_by`、`source_hash`、`created_at`。

## 2. 公共约定

- **错误**：`WritersRoomError(message, hint)`；CLI 打印 `错误：… 解决：…`，exit 2；意外异常 exit 1；成功 exit 0。确定性依赖或外部模型缺失时必须报可操作错误，禁止伪造成功。
- **确定性**：同输入同参数 → 除时间戳/run_id 外字节一致。
- **中文场景头**：识别前缀 `内/外/内外/内景/外景/内外景`（可接 `.`、`．`、`、`），及英文 `INT./EXT./INT/EXT/I-E` 等；场号取行首 `数字+.`/`#…#`/`场\d+` 或场景头尾部 `#…#`。
- **估算页数**：`chars_no_space / page_chars`，`page_chars` 默认 500（中文），可在 project.json `constraints.page_chars` 覆盖。
- **分词**：默认字符 unigram+bigram（中英文混排按 ASCII 词与 CJK 字切分）；环境装 jieba 且 `--tokenizer jieba` 时用 jieba。stats.method 记录实际所用。

## 3. 文档模型（normalized/<source_id>.json，schema 1）

```json
{
  "schema": 1,
  "source": {"id": "…", "original_path": "sources/…/original.txt",
             "format": "txt|md|fountain|docx|pdf", "sha256": "…",
             "imported_at": "ISO8601", "tool": "writersroom 0.1.0"},
  "warnings": [{"code": "…", "detail": "…", "location": null}],
  "blocks": [{"index": 0, "kind": "…", "text": "…",
              "line_start": 1, "line_end": 1,
              "scene_no": null, "episode": null, "page": null}],
  "text": "规范化纯文本全文"
}
```

- `block.kind` ∈ `heading`（集/章/部标题）、`scene_heading`、`action`、`character`、`dialogue`、`parenthetical`、`transition`、`paragraph`、`note`。
- 警告码：`ENCODING_FALLBACK`（UTF-8 失败回退编码，detail 记实际编码）、`PARSE_ERROR`、`SCANNED_PAGE`（location=页码）、`UNSUPPORTED_TYPE`、`EMPTY_TEXT`、`UNSTABLE_SOURCE`（外部不稳定来源）。
- 失败 ≠ 空正文：任何解析异常都必须转成 warnings + 尽量保留已提取内容；提取为空且无警告即 bug。

## 4. 模块规格

### 4.1 ingest（文档接收）
- 输入：文件路径；`--format auto|txt|md|fountain|docx|pdf`，auto 按扩展名+嗅探。
- txt/md：读文本（utf-8 → gb18030 回退 → 警告），md 按段落/标题切块。
- fountain：**自研解析器为主**（中文场景头/中文角色名/中文场号 + Fountain 子集，规则文档化于 ingest.parse_fountain docstring）；screenplain 仅作 `--parser screenplain` 显式指定的交叉验证后端。理由：双后端随环境差异切换破坏跨环境确定性，且中文规范化需可控规则（调研域 A 结论）。fdx 不支持 → `UNSUPPORTED_TYPE`。
- docx：python-docx 读取段落文本；缺失依赖 → `WritersRoomError("缺少 python-docx", "pip install writersroom[docx]")`。
- pdf：pypdf 逐页提取；无文本且有嵌入图像 → 该页 `SCANNED_PAGE` 警告；加密/损坏 → `PARSE_ERROR`。逐页异常隔离，单页失败不拖垮全文。
- 输出：复制到 sources/<source_id>/，写 provenance.json 与 normalized/<source_id>.json。

### 4.2 analyze（确定性分析）
- stats.json：`chars_total`、`chars_no_space`、`paragraphs`、`sentences`（。！？…；切分）、`scenes`、`episodes`、`dialogue_lines`、`dialogue_chars`（去重角色数）、`est_pages`、`dialogue_by_character`{角色:行数}、`keywords`（TF-IDF top20，含 method）、`reading_minutes`。
- signals.json（每项带 evidence=block index 列表）：
  - `active_action`：动作块以主动动词词表开头（自建词表，数据文件）的比例与命中；
  - `conflict`：冲突词表命中（每场计数）；
  - `hooks`：场尾/集尾钩子词（悬念、反转、揭示类）命中；
  - `emotion`：自研情绪词表正/负命中（否定词、程度副词规则），按场聚合；
  - `high_cost`：高成本场面词表（雨夜、车戏、爆破、群演、航拍、水下…）命中；
  - `structure`：场长分布、对白/动作比、最长场。
- 词表：`src/writersroom/data/lexicons/*.txt`，文件头注释声明「自研启发式词表，可项目内覆盖」。

### 4.3 hints（八维开发提示）
- 维度 id：`story_core` 故事核心、`genre_fit` 类型完成度、`character_drive` 人物驱动力、`relationship_tension` 关系张力、`pace_hooks` 节奏/钩子、`emotional_value` 情绪价值、`market_identity` 市场辨识度、`production_feasibility` 制作可行性。
- 每维输出：`score`（0–5 启发式）、`evidence`（block/统计引用）、`reasoning`、`risks[]`、`priority`（high|medium|low）、`suggestions[]`。
- hints.json 顶层固定 `disclaimer`：「启发式开发提示，非艺术结论或平台认证」。
- 打分规则必须可复算：每条规则 → 数据文件中的阈值表；禁止硬编码魔法数。

### 4.4 project / version / compare
- `project.json`（schema 1）：`title`、`format`（形态）、`genre[]`、`audience`、`platform`、`logline`、`goals[]`、`constraints{}`、`status`、`created_at`、`updated_at`。
- `versions.json`：`versions[] = {label, source_id, note, created_at}`；label 唯一。
- compare(v1, v2) 输出 compare.json + compare.md：
  - `stat_diff`：每统计项 {a, b, delta}；
  - `character_changes`：added/removed/frequency_delta；
  - `keyword_changes`：added/removed；
  - `dimension_diff`：每维 {a, b, delta}；
  - `issues`：以「维度 id + 风险文本」为标识，分为 new/removed/improved/regressed/persistent。

### 4.5 memory / decisions / tasks
- `candidates.jsonl` 行：`{id, kind: preference|decision|fact|note, text, source, status: candidate, created_at}`。
- `decisions.jsonl` 行：`{id, target_id, action: confirm|propose|reject|park|question, text, actor, created_at}`；候选状态 = 最新 action 推导：已确认/提案/否决/暂存/待核问题。
- `tasks gen`：汇总 待核问题 + 高优先级未决风险（最新 analysis run）+ 暂存超期项 → tasks.json（含 `why`、`source_ref`）+ tasks.md；**只生成建议，绝不自动执行**。

### 4.6 meeting（会议证据管线）
- `meeting locate --date … --type …`：定位/创建 meetings/<date>_<type>/。
- `meeting ingest <file>`：复制入 raw/ + provenance；支持 srt/vtt/txt/md/docx（第三方：Zoom VTT `姓名: 文本`、讯飞风格 TXT `[00:01:02] 说话人1：…`、纯 TXT 无时间戳）。
- `meeting normalize` → normalized.json turns：`{seq, start, end, speaker, text, confidence, source_format, source_line}`；**未知说话人保留 `UNKNOWN` 或原标签，严禁猜测命名**；无时间戳源 start/end=null。
- `meeting compact` → runs/<run_id>/ + handoff.md：连续同说话人合并为段；每段 `[HH:MM:SS] 说话人: 原文摘录（前 60 字）`；命中决策/疑问词表的行附 `[决策]`/`[疑问]` 标记；低置信或 UNKNOWN 附 `[?]`；末尾列 未决问题与候选结论（状态取自 review 层）。压缩是**摘录式**，不生成抽象摘要。
- `meeting review`：说话人更名映射、候选结论 accept/reject 写入 review/overrides.json 并追加 history.jsonl；更名只影响之后生成的 handoff，raw/normalized 不动。
- `meeting restore --seq N`：按 history.jsonl 前 N 条重建 overrides.json（历史可恢复）。

### 4.7 stage（故事开发/创作协作阶段运行器）
- 管道定义在包数据 `pipelines/*.json`；内置：
  - `new-project`：素材整理→素材拆解→定位/对标→调研问题→故事核心→多方向推演→方案评估→全季结构→分集大纲；
  - `in-dev`：材料总表→会议意见分层→版本演化→现稿复盘→定位确认→诊断→重构比较（修补/局部重构/整体重构）；
  - `collab`：计划/前提/结构/人物小传/配角对手/关系/世界/设定集/全季弧线/分集梗概/分场大纲/场景/对白/草稿/审读/连续性/去模板化/受众商业/改编/导出（20 阶段，可独立运行）；
  - `webnovel`（可选，全部 stage 标注 `unstable_external: true` 警告）。
- `stage run <pipeline> <stage>`：模板（包数据 `templates/*.md.tmpl`）+ 工作区上下文（project.json、已确认决定、前序 current.md）确定性渲染 → runs/<run_id>/output.md + manifest.json（输入哈希、参数、llm=none|endpoint）；`--use-llm` 时把模板+上下文发 LLM，产物标 `layer: llm-candidate`。
- `stage confirm <pipeline> <stage> <run_id>`：人工确认后复制为 current.md，写 history；未 confirm 的产物不进入下游上下文。
- 写前锁定检查：`stage run collab scenes` 等正文类阶段前置校验——前提/结构/人物主动选择/因果链/关系阶段/事件覆盖/集尾钩子/制作边界八项在 project.json `locks{}` 中未锁定 → 拒绝执行并列出缺失项（人工 `stage lock <key>` 锁定）。
- `stage history <pipeline> <stage>`：列出全部 run 与 confirm 记录。

### 4.8 tools（通用工具）
- **import**：`import scan <dir> [--dry-run]` → plan.json：`{file, category, target_name, action}`；分类规则=文件名正则表（剧本/大纲/会议/素材/参考）+ md frontmatter `category`；集数前缀规范化 `E1|e01|第1集|第01集 → EP01`；目标已存在 → `skip_exists` 绝不覆盖；`import apply` 执行并写 report.md。
- **kb**：`kb search <词…> [--dir knowledge/]`：解析 frontmatter（标题/标签）+ 标题行 + 正文；自研 BM25Okapi（k1=1.5, b=0.75）排序；输出 `{file, score, matched_terms, context[]}`；**无结果必须明示**「未命中」并列出已索引文件数。
- **scenes**：`scenes index <normalized.json>` → scenes.json/md：`{scene_no, heading, source_location{line_start,line_end,page}, dialogue_characters[], opening_excerpt, closing_excerpt}`；只摘录原文（截断标注 `…`），**不总结不改写**。
- **docgen**：`docgen --spec spec.json --out x.docx`：spec 提供 cover/title/sections（段落/列表/表格）/header/footer/font_cjk/固定列宽；python-docx 生成，页码用 oxml PAGE 域，中文字体写 `w:eastAsia`；生成后重开校验（节数、段落数、表格数、列宽、页脚域 XML）并打印校验报告；**spec 之外不得内置任何真实项目元数据**。

### 4.9 llm（可选适配）
- 配置解析顺序：CLI 旗标 > `WRITERSROOM_LLM_BASE_URL/_API_KEY/_MODEL` > `OPENAI_BASE_URL/OPENAI_API_KEY`；默认未启用。
- `llm check`：连通性检查；失败 → `WritersRoomError("LLM 端点不可用: …", "设置 WRITERSROOM_LLM_BASE_URL（如 http://localhost:11434/v1）或放弃 --use-llm")`。
- 调用只发用户显式指定内容；日志记录端点与字节数；超时 30s；一切确定性功能不依赖 LLM。

### 4.10 audit（运行审计）
- `scripts/audit.py` 检查：秘密模式、绝对路径（macOS/Linux 用户目录完整路径、Windows 盘符路径）、禁用产物（`*.db/*.sqlite/*.key`、音视频媒体、运行归档）、LICENSE 与 NOTICE.md 存在、samples 均为合成声明；输出机器可读 JSON + 非零退出码；外部工具（detect-secrets/pip-licenses）存在则调用，缺失则记录 skip 而非失败。

## 5. 合成样例（samples/，全部自造）

- `mini.fountain`：中文场景头+中文角色的两集迷你剧本（含钩子、冲突、高成本场面各至少一处）。
- `mini_v2.fountain`：同剧改版（统计/角色/关键词有差异）。
- `meeting_zoom.vtt`、`meeting.srt`、`meeting_iflytek.txt`、纯 TXT 笔记。
- `knowledge/` 三篇带 frontmatter 的 md。
- `import_fixture/` 混合命名文件目录。
- `docgen_spec.json` 合成文档规格。
- 每份样例文件头标注「合成样例，非真实项目资料」。

## 6. 验收标准（测试映射）

| 要求 | 验证 |
|---|---|
| 每个确定性功能有单元/性质测试 | tests/ 每模块 ≥1 测试文件；解析器含不变量测试（块索引连续、行号单调） |
| 六工具有最小可复现样例 | golden 测试：解析/比较/导入/检索/场次/文档生成各 ≥1 |
| 会议证据流程 | 集成测试：来源分层、UNKNOWN 保留、review 覆盖、restore 重建、handoff 时间戳与 `[?]` 标记 |
| 跨模块 smoke | scripts/smoke.sh：init→ingest→analyze→compare→meeting→stage→docgen 全链 |
| 确定性 | 同输入双跑，JSON 除时间戳/run_id 外一致 |
| 审计 | scripts/audit.py 全绿 |
| 文档诚实分级 | README/功能文档每能力标注：已运行验证/测试验证/代码存在/外部依赖/协议约定/未完成 |

## 7. CLI 命令清单（`writersroom <cmd>`）

`init / ingest / analyze / report / version add|list / compare / memory add|list / decision confirm|propose|reject|park|question|list / tasks gen|list / meeting locate|ingest|normalize|compact|review|restore|list / stage list|run|confirm|lock|history / import scan|apply / kb search / scenes index / docgen / llm check / audit`

退出码：0 成功；2 用户/环境错误（带解决提示）；1 内部错误。
