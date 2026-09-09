#!/usr/bin/env bash
# 端到端 smoke：init→ingest→analyze→compare→meeting→stage→import→kb→scenes→docgen
# 用法：scripts/smoke.sh [工作区目录]（默认 mktemp 临时目录）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
CLI="$PY -m writersroom.cli"
WS="${1:-$(mktemp -d)/smoke_ws}"
echo "== 工作区：$WS"

$CLI init "$WS" --title 烟测剧 >/dev/null
$CLI profile "$WS" --genre 悬疑 --format 剧集 --platform 流媒体 \
  --logline "一个人造 logline，仅用于冒烟" >/dev/null
$CLI ingest "$WS" "$ROOT/samples/mini.fountain" --source-id ep01_v1 >/dev/null
$CLI ingest "$WS" "$ROOT/samples/mini_v2.fountain" --source-id ep01_v2 >/dev/null
$CLI version add "$WS" ep01_v1 v1 >/dev/null
$CLI version add "$WS" ep01_v2 v2 >/dev/null
$CLI analyze "$WS" v1 >/dev/null 2>&1
$CLI analyze "$WS" v2 >/dev/null 2>&1
$CLI compare "$WS" v1 v2 >/dev/null

$CLI memory add "$WS" --kind decision --text "双人探案设定" >/dev/null
$CLI decision "$WS" confirm CR0001 >/dev/null
$CLI tasks gen "$WS" >/dev/null

$CLI meeting locate "$WS" --date 2026-08-20 --type 剧本会 >/dev/null
for f in meeting_zoom.vtt meeting.srt meeting_iflytek.txt meeting_notes.txt; do
  $CLI meeting ingest "$WS" 2026-08-20_剧本会 "$ROOT/samples/$f" >/dev/null
done
$CLI meeting normalize "$WS" 2026-08-20_剧本会 >/dev/null
$CLI meeting compact "$WS" 2026-08-20_剧本会 >/dev/null

$CLI stage run "$WS" new-project materials >/dev/null
RUN=$(ls "$WS/stages/new-project/materials/runs" | head -1)
$CLI stage confirm "$WS" new-project materials "$RUN" >/dev/null

$CLI import scan "$WS" "$ROOT/samples/import_fixture" >/dev/null
$CLI import apply "$WS" "$ROOT/samples/import_fixture" >/dev/null
$CLI kb search "$WS" 节奏 --dir "$ROOT/samples/knowledge" >/dev/null 2>&1
$CLI scenes index "$WS" ep01_v1 >/dev/null
$CLI docgen "$ROOT/samples/docgen_spec.json" "$WS/exports/smoke.docx" >/dev/null
$CLI llm check >/dev/null  # 未配置也应 exit 0

echo "== smoke 通过：$WS"
find "$WS" -name "*.md" -path "*analysis*" | head -3
