"""scripts/audit.py 测试（spec §4.10）。"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load_audit():
    spec = importlib.util.spec_from_file_location("writersroom_audit", REPO / "scripts" / "audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit_mod():
    return _load_audit()


def _make_clean_repo(tmp_path: Path) -> Path:
    (tmp_path / "LICENSE").write_text("MIT", encoding="utf-8")
    (tmp_path / "NOTICE.md").write_text("依赖声明", encoding="utf-8")
    (tmp_path / "samples").mkdir()
    (tmp_path / "samples" / "README.md").write_text("全部为合成样例", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    return tmp_path


def test_clean_repo_passes(audit_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_mod.shutil, "which", lambda _: None)  # 外部工具缺失→skipped
    report = audit_mod.audit(_make_clean_repo(tmp_path))
    assert report["ok"] is True
    assert any(c["skipped"] for c in report["checks"])


def test_detects_violations(audit_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_mod.shutil, "which", lambda _: None)
    root = _make_clean_repo(tmp_path)
    (root / "leak.py").write_text("api_key = \"abcdefghijklmnop12345\"\n", encoding="utf-8")
    (root / "note.md").write_text("见 /Users/someone/secret.txt\n", encoding="utf-8")
    (root / "fake.db").write_bytes(b"x")
    (root / "rec.mp3").write_bytes(b"x")
    report = audit_mod.audit(root)
    assert report["ok"] is False
    by_name = {c["name"]: c for c in report["checks"]}
    assert not by_name["secrets"]["ok"]
    assert not by_name["absolute_paths"]["ok"]
    assert not by_name["forbidden_artifacts"]["ok"]
    assert len(by_name["forbidden_artifacts"]["findings"]) == 2


def test_allowlist_exempts(audit_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_mod.shutil, "which", lambda _: None)
    root = _make_clean_repo(tmp_path)
    (root / "leak.py").write_text("api_key = \"abcdefghijklmnop12345\"\n", encoding="utf-8")
    (root / ".audit-allowlist").write_text(
        'leak.py:api_key = "abcdefghijklmnop12345"\n', encoding="utf-8")
    report = audit_mod.audit(root)
    assert {c["name"] for c in report["checks"] if not c["ok"]} == set()


def test_missing_samples_readme(audit_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_mod.shutil, "which", lambda _: None)
    root = _make_clean_repo(tmp_path)
    (root / "samples" / "README.md").unlink()
    report = audit_mod.audit(root)
    assert report["ok"] is False


def test_real_repo_passes(audit_mod):
    """真仓库必须全绿（外部工具缺失记 skipped 不失败）。"""
    report = audit_mod.audit(REPO)
    failures = [f"{c['name']}: {c['findings'][:3]}" for c in report["checks"]
                if not c["ok"] and not c["skipped"]]
    assert not failures, f"真仓库审计失败：{failures}"
