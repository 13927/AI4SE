from __future__ import annotations

from aise.codewiki_ops import _map_files_to_modules


def test_filetree_best_match_picks_first_rule():
    filetree = {
        "kind": "view.filetree",
        "version": "0.1",
        "roots": ["."],
        "mappings": [
            {
                "id": "hi",
                "match": {"kind": "glob", "value": "src/main/java/**"},
                "targets": {"modules": ["app/java"]},
                "priority": 60,
            },
            {
                "id": "lo",
                "match": {"kind": "glob", "value": "src/**"},
                "targets": {"modules": ["core"]},
                "priority": 10,
            },
        ],
    }
    # 两条都能匹配，但只应该选择高优先级的 app/java
    out = _map_files_to_modules(filetree, ["src/main/java/A.java"])
    assert out == {"app/java"}

