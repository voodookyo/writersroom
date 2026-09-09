"""开发工具：按管道定义生成阶段模板文件（一次性/模板结构变更时重跑）。

用法：.venv/bin/python scripts/dev/gen_stage_templates.py
输出：src/writersroom/data/templates/<pipeline>-<stage>.md.tmpl
模板统一骨架 + 每阶段专属小节；占位变量见 stages.build_context。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIPELINES = ROOT / "src/writersroom/data/pipelines"
TEMPLATES = ROOT / "src/writersroom/data/templates"

SECTIONS = {
    "np-materials": ["素材清单（名称/来源/类型/日期）", "素材主题归类", "可用与不可用边界（权利/事实核查状态）", "素材缺口"],
    "np-material-breakdown": ["核心事件提取", "关键人物原型", "情感锚点", "可视场景潜力", "不可改编部分"],
    "np-positioning": ["目标受众假设", "平台与形态匹配", "对标作品（≤3 部，列差异点）", "差异化卖点"],
    "np-research-questions": ["事实性问题清单", "专业顾问需求", "资料获取途径", "核实截止时间与责任人"],
    "np-story-core": ["一句话前提", "主角与其主动选择", "核心冲突", "主题问题", "情感承诺"],
    "np-directions": ["方向 A：定位差异/卖点/风险", "方向 B：定位差异/卖点/风险", "方向 C：定位差异/卖点/风险", "共同约束"],
    "np-evaluation": ["评估维度与权重", "各方向打分表（附理由）", "推荐方案与淘汰理由", "遗留风险"],
    "np-season-structure": ["季总弧线", "幕/段划分", "关键转折点", "集尾钩子矩阵", "制作边界核对"],
    "np-episode-outlines": ["每集 A/B 故事线", "每集关键事件覆盖", "每集集尾钩子", "每集场景成本提示"],
    "dev-inventory": ["材料清单（路径/类型/版本/日期）", "材料与创作环节映射", "缺失材料"],
    "dev-meeting-layering": ["会议清单", "意见分层：已确认/提案/否决/暂存/待核", "冲突意见与仲裁人"],
    "dev-version-evolution": ["版本时间线", "各版本统计变化摘要", "关键决策与版本对应关系"],
    "dev-draft-review": ["场次序列复盘", "因果链检查", "人物主动性检查", "节奏与钩子检查"],
    "dev-positioning-check": ["原定位复述", "现稿与定位偏差", "定位是否调整（人工决定）"],
    "dev-diagnosis": ["核心问题清单（按优先级）", "证据引用", "不修的风险"],
    "dev-rebuild-compare": ["方案一 修补：范围/工作量/风险", "方案二 局部重构：范围/工作量/风险", "方案三 整体重构：范围/工作量/风险", "比较结论（人工选择）"],
    "co-plan": ["目标与交付物", "里程碑与时间", "分工", "风险与依赖"],
    "co-premise": ["前提陈述", "反前提检验", "隐含主题"],
    "co-structure": ["结构模型选择", "幕/序列划分", "因果链主链", "次要情节挂接"],
    "co-character-bios": ["主角：欲望/创伤/谎言", "主角：主动选择时刻", "其他主要人物小传"],
    "co-supporting-antagonists": ["配角功能定位", "对手动机与合理性", "对手与主角的镜像关系"],
    "co-relationships": ["关系图谱", "每对关系的阶段划分", "关系变化驱动的事件"],
    "co-world": ["时空规则", "职业/制度细节", "可视化元素"],
    "co-story-bible": ["正典事实表", "术语表", "时间线", "禁止事项（连续性红线）"],
    "co-season-arc": ["全季主弧", "人物弧分配", "中段转折", "结局方向"],
    "co-episode-synopses": ["分集梗概（每集 200-400 字）", "事件覆盖核对", "集尾钩子"],
    "co-scene-outline": ["分场列表", "每场：目标/冲突/转折", "每场：对白角色与场景成本提示"],
    "co-scenes": ["场景正文写作区", "场景目标自检", "与分场大纲偏差记录"],
    "co-dialogue": ["对白正文区", "人物声口区分自检", "潜台词检查"],
    "co-draft": ["草稿正文", "本稿变更说明", "已知遗留问题"],
    "co-review": ["审读意见汇总", "分层：事实/结构/审美", "处理决定（逐条）"],
    "co-continuity": ["事实一致性检查", "时间线一致性", "道具/设定连续性"],
    "co-de-template": ["套路化表达清单", "替换方案", "保留的故意套路"],
    "co-audience-commercial": ["目标受众契合度", "商业卖点", "平台合规注意（人工判断）"],
    "co-adaptation": ["原著/素材权利状态（人工确认）", "改编策略", "忠实度与改动清单"],
    "co-export": ["输出物清单", "格式要求", "交付检查表"],
    "wn-material-analysis": ["素材分析", "悬念/爽点密度自评", "风险点"],
    "wn-market-watch": ["公开榜单观察（人工记录来源与日期）", "趋势归纳", "时效性声明"],
    "wn-platform-format": ["目标平台格式要求（人工核实）", "章节长度/节奏适配", "连载策略"],
    "wn-writing": ["正文写作区", "章节钩子自检"],
    "wn-quality-review": ["质量审阅清单", "一致性检查", "修改决定"],
}

PREFIX = {"new-project": "np", "in-dev": "dev", "collab": "co", "webnovel": "wn"}


def build(pipeline: dict, stage: dict, prev_id: str | None) -> str:
    key = f"{PREFIX[pipeline['id']]}-{stage['id']}"
    sections = SECTIONS[key]
    lines = [
        f"# {{{{stage.title}}}} — {{{{project.title}}}}",
        "",
        f"> 阶段：{stage['title']}（{pipeline['title']}）｜ 日期：{{{{today}}}}",
        "",
        "## 项目背景",
        "- 形态/类型/受众/平台：{{project.format}} / {{project.genre}} / {{project.audience}} / {{project.platform}}",
        "- Logline：{{project.logline}}",
        "- 创作目标：{{project.goals}}",
        "",
        "## 已确认决定（人工层）",
        "{{decisions.confirmed}}",
        "",
    ]
    if prev_id:
        lines += [f"## 前序阶段摘录", f"{{{{prev.{prev_id}}}}}", ""]
    lines.append(f"## 本阶段工作")
    for sec in sections:
        lines += ["", f"### {sec}", "", "（待人工填写）"]
    lines += [
        "",
        "## 证据与引用",
        "",
        "- （待人工填写：本阶段结论引用的材料 / 分析 / 会议路径）",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    count = 0
    for pj in sorted(PIPELINES.glob("*.json")):
        pipeline = json.loads(pj.read_text(encoding="utf-8"))
        prev_id = None
        for stage in pipeline["stages"]:
            name = stage["template"]
            text = build(pipeline, stage, prev_id)
            (TEMPLATES / name).write_text(text, encoding="utf-8")
            count += 1
            prev_id = stage["id"]
    print(f"生成 {count} 个模板 → {TEMPLATES}")


if __name__ == "__main__":
    main()
