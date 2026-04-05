from pathlib import Path

from aise.extractors.symbol_index import extract_cpp_symbols, extract_java_symbols


def test_extract_java_symbols_basic():
    repo_root = Path(__file__).resolve().parent
    f = repo_root / "fixtures/sample_symbols/Sample.java"
    recs = extract_java_symbols(repo_root=repo_root, file_path=f, rel_file="Sample.java")
    kinds = {r.kind for r in recs}
    assert "class" in kinds
    assert "constructor" in kinds
    assert "method" in kinds


def test_extract_cpp_symbols_basic():
    repo_root = Path(__file__).resolve().parent
    f = repo_root / "fixtures/sample_symbols/sample.cpp"
    recs = extract_cpp_symbols(repo_root=repo_root, file_path=f, rel_file="sample.cpp")
    kinds = {r.kind for r in recs}
    assert "function" in kinds
    assert "global_var" in kinds

