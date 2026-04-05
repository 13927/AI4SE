from __future__ import annotations

import json
from pathlib import Path

from aise.codewiki_ops import scan_repo


def test_scan_generates_module_files_view(tmp_path: Path):
    # minimal repo structure
    (tmp_path / "src/main/java").mkdir(parents=True)
    (tmp_path / "src/main/java/A.java").write_text("class A {}", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.10)", encoding="utf-8")
    # init basic codewiki skeleton
    (tmp_path / "docs/codewiki/views").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/codewiki/model/schemas").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/codewiki/L1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/codewiki/L1/index.json").write_text('{"kind":"index","version":"0.1","modules":[],"apis":[],"flows":[],"playbooks":[]}\n', encoding="utf-8")

    # scan should create views/module_files.json
    scan_repo(tmp_path)
    p = tmp_path / "docs/codewiki/views/module_files.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["kind"] == "view.module_files"
    assert "modules" in data

