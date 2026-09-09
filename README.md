# writersroom

本地优先的影视创作研发工具。确定性分析核心 + 可选本地 LLM；默认完全离线；人类主创拥有最终决定权。

> 当前状态：v0.1。153 项测试、端到端 smoke、确定性双跑、运行审计全部通过。
> 每项能力的验证等级见 `docs/capabilities.md`（区分已运行验证/测试验证/代码存在/外部依赖/协议约定/未完成）。

## 安装

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[docx,fountain,zh,dev]"   # 全量（含可选能力）
pip install -e .                            # 最小（txt/md/pdf/srt/vtt/frontmatter）
```

## 快速开始

```bash
writersroom init demo_ws
writersroom ingest demo_ws samples/mini.fountain --source-id ep01_v1
writersroom version add demo_ws ep01_v1 v1
writersroom analyze demo_ws v1
writersroom ingest demo_ws samples/mini_v2.fountain --source-id ep01_v2
writersroom version add demo_ws ep01_v2 v2
writersroom compare demo_ws v1 v2
scripts/smoke.sh   # 端到端全链路冒烟（临时工作区）
```

## 文档

- `docs/research/` 独立开源调研（候选矩阵、许可证/供应链审计、缺口报告、摘要）
- `docs/design/` 方案比较、ADR-001、需求规格 spec.md、威胁模型
- `docs/capabilities.md` 能力清单与诚实分级
- `docs/comparison/` 盲比报告（实现完成后）

## 边界

- 默认零网络、零遥测；可选 LLM 仅本地 OpenAI 兼容端点（Ollama/llama.cpp/LM Studio 协议），默认关闭。
- 不代替人类做 IP/版权授权、平台合规、审美取舍、最终改稿、说话人命名决定。
- `samples/` 全部为自造合成样例，不含任何真实项目资料。
