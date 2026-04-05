from __future__ import annotations

from pathlib import Path

from aise.wiki_complete import fill_repo_codewiki, guess_module_responsibility


def test_guess_module_responsibility():
    assert "CMake" in guess_module_responsibility("build/cmake")
    assert "外部 Maven 依赖" in guess_module_responsibility("ext/maven/x/y")


def test_fill_repo_codewiki_replaces_placeholders(tmp_path: Path):
    # minimal codewiki layout
    root = tmp_path / "docs/codewiki"
    (root / "L1/modules").mkdir(parents=True)
    (root / "L2/modules").mkdir(parents=True)
    (root / "views").mkdir(parents=True)

    (root / "L1/index.json").write_text(
        '{"kind":"index","version":"0.1","modules":["core"],"apis":[],"flows":[],"playbooks":[]}\n',
        encoding="utf-8",
    )
    (root / "L1/modules/core.json").write_text(
        '{"kind":"module","id":"core","name":"core","responsibility":"（自动生成）模块职责待补充","entrypoints":[],"boundaries":[{"type":"must","statement":"x"}],"dependencies":{"depends_on":[]},"public_interfaces":[{"kind":"other","id":"core","summary":"（自动生成）对外接口待补充"}],"side_effects":[],"invariants":["（自动生成）xxx"],"provenance":{"sources":[],"last_verified_commit":"","confidence":"medium"}}\n',
        encoding="utf-8",
    )
    (root / "L2/modules/core.md").write_text(
        "# core\n\n## 设计意图\n\n- （待补充）\n\n## 隐含假设\n\n- （待补充）\n\n## 修改指南\n\n- （待补充）\n",
        encoding="utf-8",
    )

    rep = fill_repo_codewiki(tmp_path, use_llm=False)
    assert rep["modules_updated"] == 1
    assert rep["l2_modules_updated"] == 1
    text = (root / "L1/modules/core.json").read_text(encoding="utf-8")
    assert "（自动生成）" not in text
    md = (root / "L2/modules/core.md").read_text(encoding="utf-8")
    assert "（待补充" not in md


def test_fill_l2_api_flow_playbook(tmp_path: Path):
    root = tmp_path / "docs/codewiki"
    (root / "L1/modules").mkdir(parents=True)
    (root / "L2/modules").mkdir(parents=True)
    (root / "L1/apis").mkdir(parents=True)
    (root / "L2/apis").mkdir(parents=True)
    (root / "L1/flows").mkdir(parents=True)
    (root / "L2/flows").mkdir(parents=True)
    (root / "L1/playbooks").mkdir(parents=True)
    (root / "L2/playbooks").mkdir(parents=True)
    (root / "views").mkdir(parents=True)

    (root / "L1/index.json").write_text(
        '{"kind":"index","version":"0.1","modules":["core"],"apis":["api/java/http/get/hello"],"flows":["flow/x"],"playbooks":["playbook/x"]}\n',
        encoding="utf-8",
    )
    (root / "L1/modules/core.json").write_text(
        '{"kind":"module","id":"core","name":"core","responsibility":"core","entrypoints":[],"boundaries":[{"type":"must","statement":"x"}],"dependencies":{"depends_on":[]},"public_interfaces":[{"kind":"other","id":"core"}],"side_effects":[],"invariants":["a"],"provenance":{"sources":[],"last_verified_commit":"","confidence":"medium"}}\n',
        encoding="utf-8",
    )
    (root / "L2/modules/core.md").write_text("# core\n\n## 设计意图\n\n- ok\n", encoding="utf-8")

    (root / "L1/apis/api/java/http/get").mkdir(parents=True, exist_ok=True)
    (root / "L1/apis/api/java/http/get/hello.json").write_text(
        '{"kind":"api","id":"api/java/http/get/hello","name":"GET /hello","summary":"s","protocol":"http","http":{"method":"GET","path":"/hello","handler":{"file":"x","symbol":"C"}},"provenance":{"sources":[],"last_verified_commit":"","confidence":"low"}}\n',
        encoding="utf-8",
    )
    (root / "L2/apis/api/java/http/get").mkdir(parents=True, exist_ok=True)
    (root / "L2/apis/api/java/http/get/hello.md").write_text(
        "# api\n\n## 用途\n\n- （待补充）\n\n## 约束\n\n- （待补充）\n\n## 示例\n\n- （待补充）\n",
        encoding="utf-8",
    )

    (root / "L1/flows/flow").mkdir(parents=True, exist_ok=True)
    (root / "L1/flows/flow/x.json").write_text(
        '{"kind":"flow","id":"flow/x","name":"x","summary":"（待补充）","stages":[{"id":"s1","summary":"y","modules":["core"],"apis":["api/java/http/get/hello"]}],"provenance":{"sources":[],"last_verified_commit":"","confidence":"low"}}\n',
        encoding="utf-8",
    )
    (root / "L2/flows/flow").mkdir(parents=True, exist_ok=True)
    (root / "L2/flows/flow/x.md").write_text("# flow\n\n## 说明\n\n- （待补充）\n", encoding="utf-8")

    (root / "L1/playbooks/playbook").mkdir(parents=True, exist_ok=True)
    (root / "L1/playbooks/playbook/x.json").write_text(
        '{"kind":"playbook","id":"playbook/x","name":"x","summary":"（待补充）","steps":["1) a"],"verifications":["pytest"],"provenance":{"sources":[],"last_verified_commit":"","confidence":"low"}}\n',
        encoding="utf-8",
    )
    (root / "L2/playbooks/playbook").mkdir(parents=True, exist_ok=True)
    (root / "L2/playbooks/playbook/x.md").write_text("# pb\n\n## 说明\n\n- （待补充）\n", encoding="utf-8")

    rep = fill_repo_codewiki(tmp_path, use_llm=False)
    assert rep["l2_apis_updated"] == 1
    assert rep["l2_flows_updated"] == 1
    assert rep["l2_playbooks_updated"] == 1
    assert "（待补充" not in (root / "L2/apis/api/java/http/get/hello.md").read_text(encoding="utf-8")
    assert "（待补充" not in (root / "L2/flows/flow/x.md").read_text(encoding="utf-8")
    assert "（待补充" not in (root / "L2/playbooks/playbook/x.md").read_text(encoding="utf-8")
