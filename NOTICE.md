# NOTICE —— 第三方依赖许可声明

writersroom 本身以 MIT 许可证发布（见 LICENSE）。以下第三方组件的许可证均与 MIT 兼容；
本表对应 docs/research/license-audit.md（2026-08-26 审计）。

## 运行时依赖

| 包 | 许可证 | 用途 |
|---|---|---|
| python-frontmatter | MIT | Markdown frontmatter 解析 |
| srt（cdown/srt） | MIT | SRT 字幕解析 |
| webvtt-py | MIT | WebVTT 转写解析 |
| pypdf（>=6.16.2） | BSD-3-Clause | 文本型 PDF 提取 |
| python-docx（可选 extras `docx`） | MIT | DOCX 读取与生成 |
| screenplain（可选 extras `fountain`，仅交叉验证后端） | MIT | Fountain 解析对照 |
| jieba（可选 extras `zh`） | MIT | 可选中文分词后端（默认回退字符 n-gram） |
| lxml（python-docx 传递依赖） | BSD-3-Clause | XML 处理 |

## 开发期工具（不进入运行时）

pytest（MIT）、pip-licenses（MIT）、detect-secrets（Apache-2.0）、cyclonedx-bom（Apache-2.0）、pip-audit（Apache-2.0，仅开发期联网查漏洞库）。

## 明确不采用的组件

PyMuPDF（AGPL/商业双许可）、pysrt（GPL-3.0）、whoosh 家族、snownlp、第三方情感词典
（NTUSD/DUTIR/BosonNLP/HowNet，均不可再分发）。理由见 docs/research/license-audit.md。

## 自研声明

BM25 检索、中文情绪词表（data/lexicons/）、中文剧本规范化、会议压缩交接、阶段运行器
均为本项目自研实现；BM25 公式以 rank_bm25（Apache-2.0）公开实现为对照参考，未复制其代码。
