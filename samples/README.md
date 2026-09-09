# samples/ —— 合成样例声明

本目录**全部为自造合成样例**，不包含任何真实剧本、小说、会议、人物、公司、版权或录音资料。
人物名（林晓、周岚、周野、林晓岚、周牧、王蔷、沈岸等）与作品名（《夜航船》等）均为虚构。

## 清单

- `mini.fountain` / `mini_v2.fountain`：两版本中文迷你剧本（fountain），用于解析/分析/版本比较 golden 测试。
- `zh_screenplay_sample.txt`：中式剧本格式（场号「1—1」+景时+人物行）合成样例，用于结构解析、页眉剔除、名册对白识别测试。
- `meeting_zoom.vtt`：Zoom 风格 VTT 转写（含 `姓名: 文本` 说话人前缀与无前缀 UNKNOWN 行）。
- `meeting.srt`：无说话人标签的 SRT（全部应归为 UNKNOWN）。
- `meeting_iflytek.txt`：讯飞听见风格 TXT（`[时间] 说话人N：文本`）。
- `meeting_notes.txt`：纯文本会议笔记（无时间戳）。
- `knowledge/`：三篇带 frontmatter 的知识库 Markdown（类型片/节奏/人物关系笔记）。
- `import_fixture/`：混合命名文件目录（第1集剧本.txt、e02_剧本.txt、第3集大纲.txt、会议纪要-20260801.txt、参考-类型片.txt、角色小传-林晓.txt、导演阐述.md、剧本围读笔记.md），用于项目导入 dry-run/分类/集数前缀规范化测试。
- `docgen_spec.json`：DOCX 生成的合成文档规格（封面/标题/列表/表格/页眉页脚/中文字体）。
