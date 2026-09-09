# 调研计划与检索问题（阶段一）

- 调研执行日期：2026-08-26
- 调研人：独立调研（仅公开来源：官方文档、源码仓库、PyPI、发布记录、issue）
- 约束：不读取、不引用本机任何现有项目；不采信网页宣传语；无法确认的事实标注「未确认」

## 证据规则

1. 优先级：官方文档 / 源码（含 LICENSE、tests、CI 配置）> 发布记录（PyPI/GitHub Releases）> issue 讨论 > 二手介绍。
2. 每个候选必须记录：链接、许可证、最近维护情况、发布/提交活跃度、测试、文档、安装难度、核心能力、缺口、隐私/供应链风险、许可证兼容性。
3. 分类只能为：直接采用 / 适配封装 / 仅参考 / 自研替代（含排除）。不得因明星项目或代码量大而直接选用。

## 检索问题与关键词

| 域 | 检索问题 | 关键词 | 为什么搜这个方向 |
|---|---|---|---|
| A 剧本格式解析 | 有无维护中、许可证兼容的 Fountain 解析器？中文场景头/角色名支持？ | `fountain screenplay parser`、`screenplain`、`screenplay-tools`、`fountain-py` | 剧本是核心输入格式，自研解析器成本高，优先找成熟候选 |
| B 文档提取/生成 | python-docx / pypdf / pdfminer.six 维护与安全状态？扫描件识别？DOCX 中文字体/页眉页脚/页码/固定列宽？ | `python-docx`、`pypdf CVE`、`pdfminer.six`、`w:eastAsia`、`PAGE field` | 文档接收与文档生成两个功能域都建立在这一层上 |
| C 转写规范化 | SRT/VTT 解析库维护状态与许可证？Zoom/腾讯会议/讯飞听见导出格式特征？ | `srt python`、`pysrt`、`webvtt-py`、`Zoom VTT transcript`、`讯飞听见 导出` | 会议证据管线的输入层；第三方格式决定适配器数量 |
| D 知识检索 | frontmatter 解析与本地 BM25 检索候选？中文分词问题？ | `python-frontmatter`、`whoosh`、`rank_bm25` | 知识检索需要可复算的相关度排序，且必须支持中文 |
| E 中文 NLP | 分词/情绪词典候选的许可证与可再分发性？ | `jieba`、`snownlp`、`NTUSD`、`DUTIR 情感词汇本体`、`BosonNLP` | 统计/结构/情绪信号必须本地可复算；词典再分发授权是关键合规问题 |
| F 可选 LLM 协议 | 本地 OpenAI 兼容端点的协议形态与默认端口？离线可行性？ | `Ollama OpenAI compatibility`、`llama.cpp server`、`LM Studio API`、`OPENAI_BASE_URL` | 只做可选适配；统一协议可一次覆盖多后端 |
| G 审计/供应链 | 许可证门禁、秘密扫描、SBOM、漏洞审计工具？ | `pip-licenses`、`detect-secrets`、`cyclonedx-bom`、`pip-audit` | 运行审计与许可证声明是最低验收要求 |
| H 编排 | 是否需要现成工作流引擎？ | `doit`、`pydoit` | 阶段运行器是核心构件，需记录「自研 vs 采用」的取舍 |

## 调研方法记录

- 网络检索工具：WebSearch + FetchURL；每个关键事实至少一个一手来源（官方文档或仓库文件）。
- 并行分为四个调查组（A+C / B / D+E / F+G+H），各自产出结构化报告，汇总为 `candidate-matrix.md`。
- 本目录其他文件：`license-audit.md`（许可证/供应链审计）、`gap-report.md`（覆盖与缺口）、`summary.md`（一页结论）。
