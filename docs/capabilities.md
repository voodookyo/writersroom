# 能力清单与诚实分级（v0.1，2026-08-26）

分级定义：
- **已运行验证**：在开发机上以合成样例实际运行过（smoke/手动链路）。
- **测试验证**：有自动化测试覆盖（tests/ 对应文件）。
- **代码存在**：实现已写但未在真实场景充分验证。
- **外部依赖**：能力依赖用户自备的外部条件（LLM 端点、真实转写样本等）。
- **协议约定**：按公开协议实现，未与真实端点联调。
- **未完成**：明确未做。

| 能力 | 分级 | 证据 |
|---|---|---|
| 工作区 init / 项目档案 / 版本标签 | 已运行验证 + 测试验证 | smoke.sh、test_cli/test_project |
| 文档接收 txt/md/fountain（含中文场景头/角色名） | 已运行验证 + 测试验证 | test_ingest、smoke |
| 文档接收 docx | 测试验证 | test_ingest::test_docx |
| 文档接收 pdf（含扫描件逐页警告、DoS 护栏） | 测试验证 | test_ingest::test_pdf_text_and_scanned（合成 PDF） |
| 中式剧本结构解析（场号「1—1」+景时+人物行，txt/docx/pdf 自动判定；页眉剔除、PDF 字间空格折叠、名册对白识别） | 已运行验证 + 测试验证 | test_ingest::test_zh_*（合成样例）；另在 12 集真实 PDF 上实测（剧本不入库） |
| 编码回退警告（GB18030 等） | 测试验证 | test_ingest::test_encoding_fallback |
| 确定性统计（字数/场次/集数/对白/估算页数/关键词等） | 已运行验证 + 测试验证 | test_analyze、双跑一致性核验 |
| 结构信号（主动行动/冲突/钩子/情绪/高成本/结构） | 测试验证 | test_analyze（启发式词表，自研） |
| 八维开发提示（证据/理由/风险/优先级/建议） | 测试验证 | test_hints；**启发式，非艺术结论** |
| 版本比较（统计差/角色/关键词/维度分差/问题五分类） | 已运行验证 + 测试验证 | test_compare、smoke |
| 候选记忆/五状态决定/任务建议 | 测试验证 | test_memory；只生成建议不执行 |
| 会议管线（定位/导入/规范化/压缩交接/review/restore） | 已运行验证 + 测试验证 | test_meetings、smoke；SRT/VTT/讯飞 TXT/纯 TXT/MD/DOCX |
| Zoom VTT 说话人抽取 | 测试验证 | 合成样例（公开格式证据，见调研域 C） |
| 腾讯会议/讯飞听见导出精确排版 | **外部依赖** | 公开文档级证据；需真实样本二次校准（gap-report 已记录） |
| 场次索引（导航，不改写正文） | 测试验证 | test_scenes |
| 知识检索（自研 BM25，中文 n-gram） | 测试验证 | test_kb；无命中明示 |
| 项目导入（分类/dry-run/EP 前缀规范化/不覆盖） | 测试验证 | test_importer |
| DOCX 生成（封面/页眉页脚/表格定宽/中文字体/页码域/重开校验） | 已运行验证 + 测试验证 | test_docgen、smoke |
| 阶段运行器（4 管道 41 阶段、locks 闸门、confirm/history） | 已运行验证 + 测试验证 | test_stages、smoke |
| 可选 LLM 适配（OpenAI 兼容本地端点） | 测试验证 + **协议约定** | test_llm 用本地假端点；未与真实 Ollama/llama.cpp 联调 |
| 网文/短篇模式 | 代码存在 + 测试验证（模板渲染）；市场观察本身**外部依赖** | test_stages::test_webnovel_unstable_warning |
| 运行审计（密钥/绝对路径/产物/许可证/samples 声明） | 已运行验证 + 测试验证 | test_audit、CLI audit 全绿 |
| 真实剧本/会议数据上的表现 | **未完成**（按任务边界只用合成样例） | — |

## 已知限制

1. 情绪/冲突/钩子等信号为自研启发式词表，命中即证据，不等于语义理解。
2. 八维提示评分由数据文件阈值驱动，供研发讨论参考，不构成艺术判断。
3. 估算页数按 500 字/页（可在 project.json constraints.page_chars 调整）。
4. PDF 仅支持文本型；扫描件只警告不 OCR（威胁模型决定）。
5. LLM 路径未与真实端点联调；其产物一律为候选，须人工 confirm。
