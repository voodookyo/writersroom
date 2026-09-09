#!/usr/bin/env python3
"""运行审计（docs/design/spec.md §4.10）。

检查：秘密模式、绝对路径、禁用产物（数据库/密钥/媒体/运行归档）、LICENSE 与
NOTICE.md 存在、samples 合成声明；外部工具 detect-secrets / pip-licenses 存在则
调用，缺失记 skipped 而非失败。

用法：
  .venv/bin/python scripts/audit.py            # 人类可读报告，违规时 exit 1
  .venv/bin/python scripts/audit.py --json     # 机器可读 JSON
误报处理：repo 根 .audit-allowlist，每行「相对路径:命中子串」显式豁免。
审计器自身与 test_audit.py 含模式字面量，内置豁免（见 SELF_EXEMPT）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SECRET_PATTERNS = [
    ("generic_api_key", re.compile(r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][^'\"\s]{12,}")),
    ("openai_key", re.compile(r"sk-[a-zA-Z0-9]{20,}")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password_assign", re.compile(r"(?i)password\s*[:=]\s*['\"][^'\"\s]{6,}")),
]
ABS_PATH = re.compile(r"(/Users/[^\s'\"]+|/home/[^\s'\"]+|[A-Za-z]:\\\\[^\s'\"]+)")

FORBIDDEN_EXT = {
    ".db", ".sqlite", ".sqlite3", ".key", ".p12", ".pem",
    ".mp3", ".mp4", ".wav", ".mov", ".aiff", ".flac",
}
SKIP_DIRS = {".venv", ".git", "__pycache__", ".pytest_cache", "dist", "build", ".idea", ".vscode"}
TEXT_EXT = {
    ".py", ".md", ".txt", ".json", ".toml", ".tmpl", ".yml", ".yaml",
    ".vtt", ".srt", ".cfg", ".ini", ".fountain", ".gitignore", ".sh",
}
SELF_EXEMPT = {"scripts/audit.py", "tests/test_audit.py"}  # 含模式字面量，内置豁免


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = set(rel.parts[:-1])
        if parts & SKIP_DIRS or any(part.endswith(".egg-info") for part in rel.parts):
            continue
        yield p, str(rel)


def _iter_text_files(root: Path):
    for p, rel in _iter_files(root):
        if p.suffix.lower() in TEXT_EXT or p.name in ("LICENSE", "NOTICE.md", ".gitignore"):
            yield p, rel


def _load_allowlist(root: Path) -> list[tuple[str, str]]:
    f = root / ".audit-allowlist"
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            path, _, pat = line.partition(":")
            out.append((path.strip(), pat.strip()))
    return out


def _exempt(allowlist, rel: str, matched: str) -> bool:
    return any(rel == path and pat in matched for path, pat in allowlist)


def check_secrets(root: Path, allowlist) -> dict:
    findings = []
    for p, rel in _iter_text_files(root):
        if rel in SELF_EXEMPT:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for name, rx in SECRET_PATTERNS:
            for m in rx.finditer(text):
                line = text[:m.start()].count("\n") + 1
                hit = f"{rel}:{line} [{name}] {m.group(0)[:40]}…"
                line_text = lines[line - 1] if line - 1 < len(lines) else ""
                if not _exempt(allowlist, rel, m.group(0)) and not _exempt(allowlist, rel, line_text):
                    findings.append(hit)
    return {"name": "secrets", "ok": not findings, "skipped": False, "findings": findings}


def check_abs_paths(root: Path, allowlist) -> dict:
    findings = []
    for p, rel in _iter_text_files(root):
        if rel in SELF_EXEMPT:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for m in ABS_PATH.finditer(text):
            line = text[:m.start()].count("\n") + 1
            hit = f"{rel}:{line} {m.group(0)[:60]}"
            line_text = lines[line - 1] if line - 1 < len(lines) else ""
            if not _exempt(allowlist, rel, m.group(0)) and not _exempt(allowlist, rel, line_text):
                findings.append(hit)
    return {"name": "absolute_paths", "ok": not findings, "skipped": False, "findings": findings}


def check_artifacts(root: Path, allowlist) -> dict:
    findings = []
    for p, rel in _iter_files(root):
        if p.suffix.lower() in FORBIDDEN_EXT and not _exempt(allowlist, rel, p.name):
            findings.append(rel)
    return {"name": "forbidden_artifacts", "ok": not findings, "skipped": False,
            "findings": findings}


def check_license_files(root: Path, _allowlist) -> dict:
    findings = [name for name in ("LICENSE", "NOTICE.md") if not (root / name).exists()]
    return {"name": "license_files", "ok": not findings, "skipped": False,
            "findings": [f"缺失：{f}" for f in findings]}


def check_samples_decl(root: Path, _allowlist) -> dict:
    f = root / "samples" / "README.md"
    if not f.exists():
        return {"name": "samples_declaration", "ok": False, "skipped": False,
                "findings": ["缺失 samples/README.md（合成样例声明）"]}
    ok = "合成样例" in f.read_text(encoding="utf-8")
    return {"name": "samples_declaration", "ok": ok, "skipped": False,
            "findings": [] if ok else ["samples/README.md 未含「合成样例」声明"]}


def check_external_tools(root: Path, _allowlist) -> list[dict]:
    checks = []
    ds = shutil.which("detect-secrets")
    if ds:
        proc = subprocess.run([ds, "scan", "--no-verify"], cwd=root,
                              capture_output=True, text=True, timeout=120)
        try:
            data = json.loads(proc.stdout or "{}")
            n = sum(len(v) for v in data.get("results", {}).values())
        except json.JSONDecodeError:
            n = -1
        checks.append({"name": "detect-secrets", "ok": proc.returncode == 0 and n == 0,
                       "skipped": False,
                       "findings": [] if n == 0 else [f"detect-secrets 报告 {n} 条（详见其输出）"]})
    else:
        checks.append({"name": "detect-secrets", "ok": True, "skipped": True,
                       "findings": ["detect-secrets 未安装，跳过（pip install detect-secrets）"]})
    pl = shutil.which("pip-licenses")
    if pl:
        proc = subprocess.run([pl, "--format=json", "--with-system"],
                              capture_output=True, text=True, timeout=120)
        try:
            rows = json.loads(proc.stdout or "[]")
            bad = [f"{r['Name']}({r['License']})" for r in rows
                   if not re.search(r"(?i)MIT|BSD|Apache|ISC|PSF|Python", r.get("License", ""))]
        except json.JSONDecodeError:
            bad = ["pip-licenses 输出无法解析"]
        checks.append({"name": "pip-licenses", "ok": not bad, "skipped": False,
                       "findings": bad})
    else:
        checks.append({"name": "pip-licenses", "ok": True, "skipped": True,
                       "findings": ["pip-licenses 未安装，跳过"]})
    return checks


def audit(repo_root: str | Path) -> dict:
    root = Path(repo_root)
    allowlist = _load_allowlist(root)
    checks = [
        check_secrets(root, allowlist),
        check_abs_paths(root, allowlist),
        check_artifacts(root, allowlist),
        check_license_files(root, allowlist),
        check_samples_decl(root, allowlist),
        *check_external_tools(root, allowlist),
    ]
    ok = all(c["ok"] or c["skipped"] for c in checks)
    return {"ok": ok, "root": str(root), "checks": checks}


def main(argv=None) -> int:
    as_json = argv and "--json" in argv
    report = audit(REPO_ROOT)
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"审计 {'通过' if report['ok'] else '失败'}：{report['root']}")
        for c in report["checks"]:
            mark = "SKIP" if c["skipped"] else ("OK  " if c["ok"] else "FAIL")
            print(f"  [{mark}] {c['name']}")
            for f in c["findings"][:20]:
                print(f"        - {f}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
