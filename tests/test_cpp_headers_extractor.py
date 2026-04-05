from __future__ import annotations

from pathlib import Path

from aise.extractors.cpp_headers import extract


def test_cpp_headers_extract_include(tmp_path: Path):
    inc = tmp_path / "include/foo"
    inc.mkdir(parents=True)
    (inc / "bar.h").write_text("// header", encoding="utf-8")
    apis = extract(tmp_path)
    assert any(a.get("protocol") == "cpp-header" and "foo/bar.h" in a.get("cpp_header", {}).get("include_path", "") for a in apis)

