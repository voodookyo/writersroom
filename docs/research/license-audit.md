# 许可证 / 供应链审计（阶段一）

- 审计日期：2026-08-26
- 基准：主项目代码 MIT；运行时依赖必须许可证兼容且可离线安装
- 本文件为人工审计结论；环境建成后的 `pip-licenses` 机器输出将附于文末（见「机器审计输出」节，实施阶段补充）

## 一、许可证明细

### 运行时依赖（计划采用）

| 包 | 版本钉法 | 许可证 | 兼容性 | 备注 |
|---|---|---|---|---|
| screenplain | >=0.12.0 | MIT | ✅ | 适配封装（中文规范化层在自研侧） |
| python-docx | >=1.2.0 | MIT | ✅ | 适配封装（eastAsia 字体、PAGE 域走 oxml 逃生舱） |
| pypdf | >=6.16.2 | BSD-3-Clause | ✅ | 2026 年 DoS 类 CVE 均已修复；加资源护栏 |
| srt | >=3.5.3 | MIT | ✅ | 零依赖 |
| webvtt-py | >=0.5.1 | MIT | ✅ | 零依赖 |
| python-frontmatter | >=1.3.0 | MIT | ✅ | 传递依赖 PyYAML（MIT） |
| jieba | >=0.42.1（可选 extras） | MIT | ✅ | 可选分词后端；dict.txt 数据权利链未确认，作残余风险记录 |
| lxml（python-docx 传递） | >=3.1.0 | BSD-3-Clause | ✅ | 有二进制 wheel |

### 开发期工具（不进入运行时依赖树）

| 工具 | 许可证 | 联网 | 用途 |
|---|---|---|---|
| pytest | MIT | 离线 | 测试 |
| pip-licenses | MIT | 离线 | 许可证门禁 |
| detect-secrets | Apache-2.0 | 离线（--no-verify） | 秘密扫描 |
| cyclonedx-bom | Apache-2.0 | 离线 | SBOM |
| pip-audit | Apache-2.0 | **联网查漏洞库** | 开发期漏洞审计，与离线运行时隔离 |

### 明确排除（附理由）

| 包/资源 | 许可证/条款 | 排除理由 |
|---|---|---|
| PyMuPDF | AGPL v3 / 商业双许可 | copyleft 传导，与 MIT 分发不兼容 |
| pysrt | GPL-3.0 | copyleft；功能已由 cdown/srt（MIT）覆盖 |
| whoosh 家族 | BSD-2（许可兼容） | 维护链两度断裂、新维护者为 AI-agent 账号 → 供应链风险，非许可证问题 |
| snownlp | MIT（许可兼容） | 情感模型为电商评论域、marshal 二进制模型 → 能力与卫生双重理由 |
| DUTIR 情感词汇本体 | 仅供科研教学 | 不可再分发 |
| NTUSD | 学术免费、无再分发授权 | 不可随 MIT 项目打包 |
| BosonNLP 词典 | 无原始许可证 | 第三方镜像自述 MIT 不可信 |
| HowNet 情感词典 | 学术免费、商用需授权 | 不可再分发 |
| LM Studio | 专有闭源 | 不可捆绑；仅作用户自备端点写入文档 |

## 二、供应链风险登记

| 风险 | 等级 | 缓解 |
|---|---|---|
| python-docx 单维护者、无 CI | 中 | 钉版本；ooxml 封装层隔离；fork 成本低（纯 Python） |
| pypdf CVE 频发（均为 DoS） | 中 | 钉 >=6.16.2；解析加内存/超时护栏；pip-audit 开发期巡检 |
| jieba 2020 冻结、dict.txt 权利链未确认 | 中 | 可选 extras 而非硬依赖；默认回退字符 n-gram 分词器 |
| screenplain 2021–2025 曾静默 | 低 | 仅作可选解析后端之一；中文规范化层自研，替换成本可控 |
| pdfminer.six 曾有 RCE 级 CVE | 低（不默认引入） | 仅备选；若引入须 >=20251230 并记录 |
| 情感/情绪词典授权链 | 高（已规避） | 不打包任何第三方词典；自研小型词表 |
| LLM 端点 | 中 | 默认离线；仅本地回环地址文档化；密钥仅环境变量 |

## 三、合规操作约定

1. 引入任何依赖前：查 LICENSE 原文 → 更新本表 → pip-licenses 复核。
2. 复制任何代码片段前：确认许可证与版权声明义务（Apache-2.0 需保留 NOTICE）；本项目目标是**不复制任何上游代码**，仅按公开规范/公式自行实现（BM25 公式以 rank_bm25 源码作对照参考，Apache-2.0 兼容）。
3. 模型权重、词典、转写样本一律不进仓库；samples/ 只含自造合成样例。
4. 发布物附 `NOTICE.md` 汇总依赖许可证（实施阶段生成）。

## 四、机器审计输出

执行日期 2026-08-26，环境 `.venv`（Python 3.12）：

- `pip-licenses --with-system`：全部 40+ 已装包中，运行时依赖树许可证为 MIT/BSD/Apache/PSF，无 copyleft 传导风险。
- 4 个被 `--from=mixed` 标记的包（**均为开发期工具的传递依赖，不进运行时**）：
  - certifi：MPL-2.0（弱 copyleft、文件级，作为依赖使用与 MIT 兼容）✅
  - fqdn：MPL-2.0（同上）✅
  - chardet：LGPL（detect-secrets 传递依赖；Python 库动态引用惯例下与 MIT 分发兼容，仅开发期使用）✅
  - defusedxml：PSFL（兼容）✅
- `scripts/audit.py`（含 pip-licenses/detect-secrets 接入）：全绿，见 `tests/test_audit.py::test_real_repo_passes`。
- pip-audit 未在本机执行（需联网查漏洞库）；开发期可选运行，联网属性见威胁模型。
