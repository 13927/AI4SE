from __future__ import annotations

from aise.config import AiseConfig


def test_verify_allowlist_prefix_match():
    cfg = AiseConfig(verify_allowlist=["pytest", "mvn test"])
    cmd1 = "pytest -q"
    cmd2 = "mvn test -q"
    cmd3 = "rm -rf /"
    assert any(cmd1 == a or cmd1.startswith(a + " ") for a in cfg.verify_allowlist)
    assert any(cmd2 == a or cmd2.startswith(a + " ") for a in cfg.verify_allowlist)
    assert not any(cmd3 == a or cmd3.startswith(a + " ") for a in cfg.verify_allowlist)

