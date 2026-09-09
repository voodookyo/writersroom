# 候选矩阵（阶段一）

- 检索日期：2026-08-26（所有「活跃/最近」判断以此为准）
- 许可证兼容性基准：主项目 MIT
- 分类：直接采用 / 适配封装 / 仅参考 / 自研替代（含排除）

## 域 A：剧本格式解析

### screenplain —— 适配封装 ✅
- 链接：https://github.com/vilcans/screenplain ｜ https://pypi.org/project/screenplain/
- 许可证：MIT（LICENSE.txt / GitHub API / README 三处一致）→ 兼容
- 维护：最后推送 2026-04-28（v0.12.0，PyPI 同日发布）；2021–2025 曾长期静默，2026 恢复维护
- 测试/CI：tests/ + pytest + ruff；GitHub Actions 在 py3.10/3.11/3.13 跑单测与 CLI 冒烟
- 文档：README 够用，无独立 API 站；安装：`pip install screenplain`，纯 Python，运行依赖 reportlab（PDF 输出用）
- 核心能力：Fountain 解析 → FDX/HTML/PDF；可库化调用
- 缺口（源码级确认）：场景头正则硬编码 `INT|EXT|EST|I/E`；角色判定用 `str.isupper()`，中文人名不识别；中文转场同理。缓解：`.` 强制场景头、`@` 强制角色可绕过
- 隐私/供应链：低；单维护者；无网络行为
- 理由：解析核心完整、许可兼容、近期恢复维护；中文支持需外层规范化 → 适配封装

### wildwinter/screenplay-tools —— 仅参考（备选封装）
- 链接：https://github.com/wildwinter/screenplay-tools ｜ 许可证：MIT → 兼容
- 维护：创建 2024-11，最后推送 2026-01-05（v0.0.10）；22 stars；无 CI
- 测试：四语言测试目录 + 共享 fountain fixtures；文档：README 详尽
- 安装：非标准（GitHub Release zip；PyPI 页面存在但版本未确认）
- 核心能力：Fountain/FDX 双向；CallbackParser（角色+对白聚合回调）贴合台词提取
- 缺口：同样不支持中文场景头/角色名/场号；供应链风险中（单维护者、无 CI、分发不规范）
- 理由：设计可参考，项目年轻且分发不规范 → 仅参考/备选

### fountain-py 谱系 / Jouvence —— 仅参考 / 排除
- Tagirijus/fountain（MIT，2026-04 仍推送）与 wildwinter fork（已归档，许可证 NOASSERTION 未确认）→ 功能子集，仅参考
- Jouvence（Apache-2.0，2020-09 后停滞，自述无完善 Unicode 支持）→ 排除采用，仅参考

### Fountain 官方规范（fountain.io/syntax）
- 场景头前缀表与全大写角色启发式是纯英文约定；官方明确 `@` 强制角色「对非罗马语言有用」
- **领域级缺口**：无任何候选库原生支持中文场景头（内./外./内景/外景）、中文角色名、中文场号 → 中文规范化层必须自研

## 域 B：文档提取与生成

### python-docx —— 适配封装 ✅
- 链接：https://github.com/python-openxml/python-docx ｜ 许可证：MIT → 兼容
- 维护：1.2.0（2025-06-16）；节奏约一年一版；实质单维护者（bus factor=1）；无 GitHub Actions（仅遗留 .travis.yml）
- 测试：完整 pytest 套件 + behave BDD；文档：ReadTheDocs 良好；安装：纯 Python + lxml
- 能力核实：页眉页脚 ✅ 原生 API；封面 ✅（`different_first_page_header_footer`+分节）；固定列宽 ✅（`autofit=False`+列宽）；**页码字段 ❌ 无字段 API**（issue #1297 确认，需 oxml 逃生舱插 `w:fldChar`）；**中文字体 ⚠️ 半支持**（`Font.name` 不映射 `w:eastAsia`，需 oxml 直写）
- 理由：核心覆盖，两个缺口各需几十行 oxml 封装 → 适配封装

### pypdf —— 直接采用 ✅（钉版本）
- 链接：https://github.com/py-pdf/pypdf ｜ 许可证：BSD-3-Clause → 兼容
- 维护：6.16.2（2026-08-23，调研前 3 天）；周级发布；py-pdf 组织治理；CI 完善（含 zizmor 安全扫描）
- 安全：2026 年 CVE 密集但全部为 DoS 类且截至 2026-08-26 均已修复；**钉 `pypdf>=6.16.2`（最低 6.14.2）**，应用侧加内存/超时护栏
- 扫描件检测：✅ 官方文档背书——逐页 `extract_text()` 去空白为空 + 有嵌入图像 → 判扫描页；OCR 过的扫描件与纯矢量页为已知边缘情况
- 理由：活跃、纯 Python、离线、扫描件检测只需薄封装 → 直接采用

### pdfminer.six —— 仅参考（备选）
- MIT → 兼容；最新 20260107；维护带宽有限（README 自述）；有 RCE 级 CVE 历史（CVE-2025-64512，已修复）
- CJK/布局分析强于 pypdf，但只读、慢、依赖 cryptography；仅在 pypdf 提取质量不足时按需引入（须 ≥20251230）

### PyMuPDF —— 排除 ❌
- AGPL v3 / Artifex 商业双许可；主项目 MIT 对外分发时 copyleft 传导 → 不兼容；能力再强也不进依赖树，仅作能力基准参考

## 域 C：转写规范化

### cdown/srt —— 直接采用 ✅
- 链接：https://github.com/cdown/srt ｜ 许可证：MIT → 兼容；零运行依赖
- 维护：3.5.3（2023-03），仓库 2024-03 后低频；库已成熟
- 测试：Hypothesis 属性测试 + 自称 100% 分支覆盖 + CI；文档：ReadTheDocs
- 能力：SRT 解析/修改/生成，容错定位明确（破损文件修复），支持全角亚洲风格 SRT
- 理由：MIT、零依赖、容错强、测试充分 → 直接采用

### byroot/pysrt —— 排除 ❌
- **GPL-3.0**（GitHub API/FSF 目录一致）→ 与 MIT 分发不兼容；2020 后无发布；功能已被 cdown/srt 覆盖

### webvtt-py —— 直接采用 ✅
- 链接：https://github.com/glut23/webvtt-py ｜ 许可证：MIT → 兼容；零依赖；0.5.1（2024-05）；有 CI + 测试 + ReadTheDocs
- 能力：VTT 读写、SRT/SBV 转换；覆盖 Zoom 类 VTT 转写
- 缺口：说话人仅是 cue 正文文本（`Name: 文本`），说话人语义抽取需自研薄封装

### 第三方转写格式（公开文档级证据）
- **Zoom**：云录制转写官方确认为 VTT；实测样本：`姓名: 文本` 内联说话人、时间戳箭头两侧可无空格、无置信度字段
- **腾讯会议**：官方确认可导出 PDF/Word/纯文本；导出文件内部精确排版**未确认**，需真实样本二次确认
- **讯飞听见**：官方确认导出 Word/TXT/SRT，可勾选「显示说话人、显示时间码」；说话人标签精确写法与置信度字段**未确认**
- 结论：各源写独立导入器，统一归一化为「说话人 + 可选时间戳 + 文本 + 来源/置信标记」内部模型

## 域 D：知识检索

### python-frontmatter —— 直接采用 ✅
- 链接：https://github.com/eyeseast/python-frontmatter ｜ MIT → 兼容；v1.3.0（2026 年中）；pytest + CI；依赖仅 PyYAML
- 能力：YAML/JSON/TOML front matter 解析与回写，默认 `yaml.safe_load`；缺口：不解析 Markdown 正文结构（标题/标签需自研扫描）

### whoosh 家族 —— 仅参考 ❌（不采用）
- whoosh-community fork 官方横幅「Not maintained」；whoosh-reloaded 自述「NO LONGER MAINTAINED」；whoosh3 由自述为 AI agent 的账号接管维护（供应链风险）
- 中文需外挂 jieba ChineseAnalyzer；对小型 CLI 过重

### rank_bm25 —— 仅参考，自研替代 ✅
- Apache-2.0 → 兼容；0.2.2（2022-02）后无发布；测试仅一个 814 字节 loading 用例；硬依赖 numpy；不做分词
- 取舍：BM25Okapi 公式极小，纯标准库自研约百行，去 numpy 依赖，与自选分词器同模块闭环、行为完全确定、可 golden 测试 → **自研替代**（以 rank_bm25 源码为公式对照参考）

### 中文检索分词 —— 自研字符 unigram+bigram 为默认，jieba 可选后端
- 证据：rank_bm25 不做预处理，whoosh 需 jieba 才能索引中文 → 中文分词层必须自备
- 字符 unigram+bigram：零依赖、完全确定、对短文本/中英混合召回稳健

## 域 E：中文 NLP

### jieba —— 适配封装（可选后端）✅
- 链接：https://github.com/fxsjy/jieba ｜ MIT → 兼容；冻结于 0.42.1（2020-01）；无 CI；零运行时依赖；纯 Python
- 能力：三种分词模式、自定义词典、TF-IDF/TextRank 关键词；~19MB 内置词典（dict.txt 数据权利链**未确认**——作者自述网络语料训练，记录为残余风险）
- 用法：薄封装隔离，接口可替换为字符 n-gram；缺依赖时回退，不报错伪造

### snownlp —— 仅参考 ❌
- MIT；2020 后停滞；情感模型训练数据为电商评论域（README 自认），与影视文本错位；marshal 二进制模型属供应链卫生瑕疵

### 中文情感词典（NTUSD / DUTIR / BosonNLP / HowNet）—— 全部排除再分发 ❌
- DUTIR：官方明示「仅供科研及教学使用」→ 不可再分发
- NTUSD：学术免费，流通镜像无原始许可证 → 授权未确认（倾向于无）
- BosonNLP：原始发布无许可证，第三方镜像自述 MIT 不可信
- 结论：**自研小型启发式情绪词表**（数百词规模 + 否定词/程度副词规则，源自无版权争议的公开语法知识，完全自有版权）；文档指引学术用户自行申请词典以插件方式加载

## 域 F：可选 LLM 协议

- **统一结论**：实现单一 OpenAI 兼容客户端（`POST {base_url}/chat/completions`），环境变量 `WRITERSROOM_LLM_BASE_URL` / `WRITERSROOM_LLM_API_KEY`（兼容读 `OPENAI_BASE_URL`/`OPENAI_API_KEY`），默认关闭
- **Ollama**（MIT）：`http://localhost:11434/v1`，api_key 任意非空占位（官方「required but ignored」）；拉模型需联网，推理纯本地 → 适配封装（首选后端之一）
- **llama.cpp server**（MIT）：`http://127.0.0.1:8080/v1`，默认无鉴权，显式 `--offline` 参数（官方参数级离线证据）→ 适配封装（首选后端之一）
- **LM Studio**（专有闭源）：`http://localhost:1234/v1`，默认免鉴权；协议兼容故零成本支持，但闭源不可捆绑 → 仅参考/文档引导
- 实现取舍：裸 `urllib`/`http.client` 调 REST，零新增依赖，规避 openai SDK（其许可证本次未核验）

## 域 G：审计/供应链工具（全部为开发期工具，不进运行时）

| 工具 | 许可证 | 维护 | 用途 | 联网 | 分类 |
|---|---|---|---|---|---|
| pip-licenses | MIT | 5.5.5（2026-03），维护者已交接恢复活跃 | 许可证门禁 `--fail-on/--allow-only` | 离线 | 直接采用 |
| detect-secrets | Apache-2.0 | v1.5.0（2024），维护放缓但成熟，有活跃 fork | 秘密扫描基线 | 离线（`--no-verify`） | 直接采用（开发期） |
| cyclonedx-bom | Apache-2.0 | 7.2.2（2026-02），OWASP 活跃 | 发布物 SBOM | 离线 | 直接采用 |
| pip-audit | Apache-2.0 | 2.10.1（2026-06），PyPA 官方 | 依赖漏洞审计 | **需联网查漏洞库** | 直接采用（仅开发期，文档注明） |

## 域 H：编排

### doit —— 仅参考，自研替代 ✅
- MIT；0.37.0（2026-02）但此前近 4 年发布空窗；个人项目 bus-factor 明显
- 缺口：任务状态存 `.doit.db`（dbm 跨平台坑历史）；DAG 引擎对线性阶段流程过重
- 结论：**自研轻量阶段运行器**（阶段产物落盘 + 每阶段 manifest：输入哈希/参数快照，纯 JSON，可重跑可审计，核心百行量级）；doit 的 uptodate/file_dep/clean 语义仅作参考

## 未确认事项汇总（诚实记录）

1. screenplay-tools 的 PyPI 发布详情；screenplain 中文场号 `#一#` 强制场景头下实测行为
2. 腾讯会议/讯飞听见导出文件内部精确排版（需真实样本，当前为公开文档级证据）；讯飞导出是否含置信度
3. jieba 在 Python 3.12+ 的官方兼容性验证（纯 Python 实际可用，未获官方确认）；jieba dict.txt 数据权利链
4. whoosh 各 fork 当前 CI 实际通过状态；snownlp 0.12.3 精确上传日期；NTUSD 官方分发页条款原文（站点本次不可达）
5. openai SDK 许可证文本（未核验；裸 HTTP 实现可规避该依赖）；detect-secrets v1.5.0 精确发布日期
