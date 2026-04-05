from __future__ import annotations

from aise.approvals import touched_files_from_patch


def test_touched_files_from_patch():
    patch = (
        "diff --git a/a.txt b/a.txt\n"
        "index 111..222 100644\n"
        "--- a/a.txt\n"
        "+++ b/a.txt\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
        "diff --git a/b/c.txt b/b/c.txt\n"
        "index 111..222 100644\n"
        "--- a/b/c.txt\n"
        "+++ b/b/c.txt\n"
        "@@ -1 +1 @@\n"
        "-x\n"
        "+y\n"
    )
    assert touched_files_from_patch(patch) == ["a.txt", "b/c.txt"]

