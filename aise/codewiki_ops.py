from __future__ import annotations

import json
import fnmatch
import hashlib
from dataclasses import dataclass
from datetime import datetime
import re
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from .git_utils import changed_files, ensure_git, head_commit
from .config import load_config
from .path_match import MatchRule, match_glob
from .schema_loader import load_embedded_schema
from .codewiki_templates import (
    default_l1_index,
    default_views_entrypoints,
    default_views_filetree,
    ensure_dirs,
    write_json,
)
from .extractors.cmake import extract as extract_cmake
from .extractors.maven import extract as extract_maven
from .extractors.spring import extract as extract_spring
from .extractors.java_rest import extract as extract_java_rest
from .extractors.cpp_headers import extract as extract_cpp_headers
from .extractors.symbol_index import extract_java_symbols, extract_cpp_symbols
from .llm_openai import OpenAIClient, load_openai_config


CODEWIKI_DIR = Path("docs/codewiki")


@dataclass
class Finding:
    rule_id: str
    severity: str  # error|warn|info
    target: str
    path: str
    message: str
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "target": self.target,
            "path": self.path,
            "message": self.message,
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def init_repo(cwd: Path, command_name: str = "app") -> None:
    ensure_git(cwd)

    root = cwd / CODEWIKI_DIR
    ensure_dirs(root)

    # 写入 views（可编辑）
    write_json(root / "views/entrypoints.json", default_views_entrypoints(command_name))
    write_json(root / "views/filetree.json", default_views_filetree("src"))

    # 写入 L1 index（空集合）
    idx_path = root / "L1/index.json"
    if not idx_path.exists():
        write_json(idx_path, default_l1_index())

    # 写入 schemas（透明化，便于 IDE/CI 校验）
    schemas_to_copy = [
        "common.schema.json",
        "index.schema.json",
        "module.schema.json",
        "api.schema.json",
        "flow.schema.json",
        "playbook.schema.json",
        "relations.schema.json",
        "views-entry-graph.schema.json",
        "views-entrypoints.schema.json",
        "views-filetree.schema.json",
        "views-symbol-index.schema.json",
        "views-module-files.schema.json",
        "views-module-symbols.schema.json",
        "views-java-http-routes.schema.json",
    ]
    for name in schemas_to_copy:
        schema = load_embedded_schema(name)
        write_json(root / "model/schemas" / name, schema)

    # README：解释 codewiki 目录结构（不覆盖已有文件）
    readme = root / "README.md"
    if not readme.exists():
        readme.write_text(
            (
                "# CodeWiki（docs/codewiki）\n\n"
                "本目录由 `aise init/scan/fill/export` 生成与维护，用于把仓库的工程事实与维护手册收敛成可交付 Wiki。\n\n"
                "## 目标（面向工程协作）\n\n"
                "- 模块能清晰划分功能，并能追溯“模块包含哪些文件”（可审计）。\n"
                "- 能解释模块间协作关系（依赖/链路），逐步从构建依赖扩展到源码级证据。\n\n"
                "## 规范（强定义）\n\n"
                "- 本仓库内的 CodeWiki 规范源：`docs/codewiki/DESIGN.md`（在 aise 项目中）。\n\n"
                "## 目录结构\n\n"
                "- `L1/`：结构化事实源（JSON，**机器可读**，用于校验/影响分析/agent）。\n"
                "  - `L1/index.json`：索引（modules/apis/flows/playbooks 列表）。\n"
                "  - `L1/modules/**.json`：模块定义（职责/依赖/不变量/边界）。\n"
                "  - `L1/apis/**.json`：接口清单（Java REST、C++ public headers 等）。\n"
                "  - `L1/flows/**.json`：关键链路（stages）。\n"
                "  - `L1/playbooks/**.json`：常见改动手册（steps/verifications）。\n"
                "- `L2/`：人类可读文档（Markdown）。与 L1 一一对应，承载更详细说明/示例/约束。\n"
                "- `views/`：派生视图（JSON）。用于把「文件/入口点」映射到模块，支持 diff gate 与增量更新。\n"
                "  - `views/filetree.json`：路径 -> modules 的映射规则。\n"
                "  - `views/entrypoints.json`：入口点视图（如 Spring Boot main、CMake build）。\n"
                "  - `views/module_files.json`：模块成员清单（module -> files[] + 统计），用于让模块归属“更硬”。\n"
                "  - `views/module_symbols.json`：模块符号清单（module -> classes 等摘要，v1：Java），用于语义化与关系抽取。\n"
                "- `model/schemas/`：JSON Schema（用于 `aise validate --mode static` 校验 L1 与 views）。\n"
                "- `WIKI.md`：单文件导出的交付版 Wiki（由 `aise export` 生成）。\n\n"
                "## 常用命令\n\n"
                "```bash\n"
                "# 生成/更新 L1 + views\n"
                "aise scan\n\n"
                "# 补齐 L1/L2 中的占位内容（不会覆盖非占位内容）\n"
                "aise fill\n\n"
                "# 导出单文件 Wiki（--strict：占位>0 则失败，便于 CI 卡口）\n"
                "aise export --out docs/codewiki/WIKI.md --strict\n\n"
                "# 校验结构（schema + 引用存在性）\n"
                "aise validate --mode static\n"
                "```\n"
            ),
            encoding="utf-8",
        )


def _module_path(root: Path, module_id: str) -> Path:
    """
    moduleId 允许包含 /，并直接映射为子目录。
    """
    return root / "L1/modules" / Path(module_id + ".json")


def _ensure_module_l1(
    root: Path,
    module_id: str,
    commit: str,
    sources: list[dict[str, str]],
    *,
    depends_on: list[str] | None = None,
) -> None:
    """
    先粗后细：如果 module 文件不存在则生成最小可执行条目；如果存在则只刷新 provenance（不覆盖人工编辑）。
    """
    mod_path = _module_path(root, module_id)
    mod_path.parent.mkdir(parents=True, exist_ok=True)

    if mod_path.exists():
        mod = _read_json(mod_path)
        if isinstance(mod, dict) and "provenance" in mod and isinstance(mod["provenance"], dict):
            mod["provenance"]["last_verified_commit"] = commit
            # 不强行追加 sources，避免不断膨胀；默认只刷新 commit。
            # Phase 4：在不覆盖人工编辑的前提下，允许对“自动生成模块”做最小回填：
            # - 若 dependencies.depends_on 为空且传入 depends_on，则回填（用于 build/maven -> ext/maven/* 等依赖图）
            if depends_on:
                deps_obj = mod.get("dependencies")
                cur = []
                if isinstance(deps_obj, dict):
                    cur = deps_obj.get("depends_on") or []
                is_auto = any(
                    isinstance(s, dict) and s.get("kind") == "command" and s.get("ref") == "aise scan"
                    for s in (mod.get("provenance", {}) or {}).get("sources", []) or []
                )
                if is_auto and (not cur):
                    mod.setdefault("dependencies", {})
                    if isinstance(mod["dependencies"], dict):
                        mod["dependencies"]["depends_on"] = list(depends_on)
            write_json(mod_path, mod)
        return

    # 最小可执行 module（满足 schema required）
    module_kind = "unknown"
    layer = "unknown"
    if module_id.startswith("build/"):
        module_kind = "build"
        layer = "build"
    elif module_id.startswith("ext/"):
        module_kind = "external"
        layer = "external"
    else:
        module_kind = "code"

    data: dict[str, Any] = {
        "kind": "module",
        "id": module_id,
        "name": module_id,
        "module_kind": module_kind,
        "layer": layer,
        "responsibility": "（自动生成）模块职责待补充：请在 L2 或本文件中完善。",
        "entrypoints": [],
        "boundaries": [
            {
                "type": "must",
                "statement": "修改该模块时必须同步更新 docs/codewiki/L1 中对应条目，并通过 aise validate。",
            }
        ],
        "dependencies": {"depends_on": depends_on or []},
        "public_interfaces": [
            {"kind": "other", "id": module_id, "summary": "（自动生成）对外接口待补充"}
        ],
        "side_effects": [],
        "invariants": ["（自动生成）保持模块边界清晰；跨模块修改需显式说明与评审。"],
        "provenance": {
            "sources": sources,
            "last_verified_commit": commit,
            "confidence": "medium",
        },
    }
    write_json(mod_path, data)


def scan_repo(cwd: Path) -> None:
    """
    冷启动扫描（MVP）：只做“可维护的粗粒度 views”生成。
    后续再逐步扩展：入口识别、API/flow 抽取、依赖图等。
    """
    ensure_git(cwd)
    root = cwd / CODEWIKI_DIR
    ensure_dirs(root)

    cfg = load_config(cwd)

    # 刷新 schemas（透明化；避免 init 早期生成的 schema 过时）
    for name in [
        "common.schema.json",
        "index.schema.json",
        "module.schema.json",
        "api.schema.json",
        "flow.schema.json",
        "playbook.schema.json",
        "relations.schema.json",
        "views-entry-graph.schema.json",
        "views-entrypoints.schema.json",
        "views-filetree.schema.json",
        "views-symbol-index.schema.json",
        "views-module-files.schema.json",
        "views-module-symbols.schema.json",
        "views-java-http-routes.schema.json",
    ]:
        try:
            write_json(root / "model/schemas" / name, load_embedded_schema(name))
        except Exception:
            pass

    # roots：优先使用配置的第一个 root；若不存在则退化为 "."
    preferred_root = (cfg.roots[0] if cfg.roots else "src") if cfg.roots is not None else "src"
    src_root = preferred_root if (cwd / preferred_root).exists() else "."

    commit = head_commit(cwd)

    # filetree：先粗后细（全 glob，易维护）
    mappings: list[dict[str, Any]] = []

    def add_mapping(
        *,
        id: str,
        kind: str,
        value: str,
        modules: list[str],
        summary: str,
        priority: int,
        source_ref: str,
        source_kind: str = "file",
    ) -> None:
        mappings.append(
            {
                "id": id,
                "match": {"kind": kind, "value": value},
                "summary": summary,
                "targets": {"modules": modules},
                "priority": priority,
                "source": {"kind": source_kind, "ref": source_ref},
            }
        )
    # --- TS/Node 风格（类似 Claude Code）---
    if (cwd / "src/main.tsx").exists():
        add_mapping(
            id="src.main",
            kind="prefix",
            value="src/main.tsx",
            modules=["cli"],
            summary="入口文件（TS/CLI）",
            priority=100,
            source_ref="src/main.tsx",
        )
    if (cwd / "src").exists() and list((cwd / "src").glob("query*.ts")):
        add_mapping(
            id="src.query",
            kind="glob",
            value="src/query*.ts",
            modules=["runtime/query"],
            summary="核心 query loop（TS）",
            priority=90,
            source_ref="src/query.ts",
        )
    if (cwd / "src/tools").exists():
        add_mapping(
            id="src.tools",
            kind="glob",
            value="src/tools/**",
            modules=["runtime/tools"],
            summary="工具系统（TS）",
            priority=80,
            source_ref="src/tools.ts",
        )
    if (cwd / "src/services/mcp").exists():
        add_mapping(
            id="src.mcp",
            kind="glob",
            value="src/services/mcp/**",
            modules=["integrations/mcp"],
            summary="MCP 集成（TS）",
            priority=80,
            source_ref="src/services/mcp/client.ts",
        )

    # --- Java/Maven/Gradle（通用启发式）---
    if (cwd / "pom.xml").exists():
        add_mapping(
            id="build.maven",
            kind="prefix",
            value="pom.xml",
            modules=["build/maven"],
            summary="构建系统（Maven）",
            priority=95,
            source_ref="pom.xml",
        )
    if (cwd / "build.gradle").exists() or (cwd / "build.gradle.kts").exists():
        add_mapping(
            id="build.gradle",
            kind="glob",
            value="build.gradle*",
            modules=["build/gradle"],
            summary="构建系统（Gradle）",
            priority=95,
            source_ref="build.gradle*",
            source_kind="other",
        )
    if (cwd / "src/main/java").exists():
        add_mapping(
            id="java.main",
            kind="glob",
            value="src/main/java/**",
            modules=["app/java"],
            summary="应用主代码（Java）",
            priority=60,
            source_ref="src/main/java",
        )
    if (cwd / "src/test/java").exists():
        add_mapping(
            id="java.test",
            kind="glob",
            value="src/test/java/**",
            modules=["test/java"],
            summary="测试代码（Java）",
            priority=55,
            source_ref="src/test/java",
        )
    if (cwd / "src/main/resources").exists():
        add_mapping(
            id="java.resources",
            kind="glob",
            value="src/main/resources/**",
            modules=["app/resources"],
            summary="资源文件（Java）",
            priority=50,
            source_ref="src/main/resources",
        )

    # --- C/C++（CMake/通用目录）---
    if (cwd / "CMakeLists.txt").exists():
        add_mapping(
            id="build.cmake",
            kind="prefix",
            value="CMakeLists.txt",
            modules=["build/cmake"],
            summary="构建系统（CMake）",
            priority=95,
            source_ref="CMakeLists.txt",
        )
    if (cwd / "cmake").exists():
        add_mapping(
            id="build.cmake.dir",
            kind="glob",
            value="cmake/**",
            modules=["build/cmake"],
            summary="CMake 模块/脚本",
            priority=90,
            source_ref="cmake/",
            source_kind="other",
        )
    if (cwd / "include").exists():
        add_mapping(
            id="cpp.include",
            kind="glob",
            value="include/**",
            modules=["cpp/include"],
            summary="公共头文件（C/C++）",
            priority=70,
            source_ref="include/",
            source_kind="other",
        )
    if (cwd / "src").exists() and any((cwd / "src").glob("**/*")):
        # 注意：Java 也会有 src。为了避免“累计命中”导致 Java repo 被误归类到 cpp/src：
        # - 仅当仓库明显是 C/C++ 项目时才启用该映射（例如存在 CMakeLists 或 include/）
        is_cpp_repo = (cwd / "CMakeLists.txt").exists() or (cwd / "include").exists()
        if is_cpp_repo:
            add_mapping(
                id="cpp.src",
                kind="glob",
                value="src/**",
                modules=["cpp/src"],
                summary="源码目录（C/C++ 项目常见 src；为避免误伤 Java，仅在检测到 CMake/include 时启用）",
                priority=40,
                source_ref="src/",
                source_kind="other",
            )
    for test_dir in ("test", "tests"):
        if (cwd / test_dir).exists():
            add_mapping(
                id=f"cpp.{test_dir}",
                kind="glob",
                value=f"{test_dir}/**",
                modules=["cpp/tests"],
                summary=f"测试目录（{test_dir}，常见于 C/C++）",
                priority=45,
                source_ref=f"{test_dir}/",
                source_kind="other",
            )
    # googletest 特征目录（C++）
    if (cwd / "googletest").exists():
        add_mapping(
            id="cpp.googletest",
            kind="glob",
            value="googletest/**",
            modules=["cpp/googletest"],
            summary="googletest 库代码",
            priority=65,
            source_ref="googletest/",
            source_kind="other",
        )
    if (cwd / "googlemock").exists():
        add_mapping(
            id="cpp.googlemock",
            kind="glob",
            value="googlemock/**",
            modules=["cpp/googlemock"],
            summary="googlemock 库代码",
            priority=65,
            source_ref="googlemock/",
            source_kind="other",
        )

    # 兜底（始终保证任意文件可映射到 core，便于 diff gate 与增量定位）
    if src_root != ".":
        add_mapping(
            id=f"{src_root}.all",
            kind="glob",
            value=f"{src_root}/**",
            modules=["core"],
            summary=f"兜底映射：所有 {src_root} 文件默认归到 core",
            priority=1,
            source_ref="scan",
            source_kind="other",
        )
        add_mapping(
            id="repo.all",
            kind="glob",
            value="**",
            modules=["core"],
            summary="兜底映射：仓库内任意文件默认归到 core（用于 diff/增量定位）",
            priority=0,
            source_ref="scan",
            source_kind="other",
        )
    else:
        add_mapping(
            id="repo.all",
            kind="glob",
            value="**",
            modules=["core"],
            summary="兜底映射：仓库内任意文件默认归到 core（用于 diff/增量定位）",
            priority=0,
            source_ref="scan",
            source_kind="other",
        )

    # ===== Phase 3：API/Flow/Playbook（v1）=====
    # 注意：全自动治理模式下，filetree 可能由 LLM 重写；但 filetree 又是 module_files/module_symbols 的输入。
    # 这里采用“两段式”：
    # 1) 先用启发式生成一个可用 filetree，生成第一版 views（routes/symbols）
    # 2) 若配置允许且模型可用，则用 LLM 重写 filetree，并据此再生成一遍关键 views，确保最终产物与 filetree 一致。
    java_apis = extract_java_rest(cwd)
    cpp_apis = extract_cpp_headers(cwd)

    # Phase 5-C（弱先验）：基于 HTTP handler 路径推断业务模块（biz/*）
    def _infer_biz_mappings_from_java_apis() -> None:
        feats: dict[str, int] = {}
        for a in java_apis:
            http = a.get("http") if isinstance(a, dict) else None
            handler = http.get("handler") if isinstance(http, dict) else None
            f = handler.get("file") if isinstance(handler, dict) else None
            if not isinstance(f, str):
                continue
            if not f.endswith("Controller.java"):
                continue
            m = re.search(r"/petclinic/([^/]+)/", f)
            if not m:
                continue
            feat = m.group(1).lower()
            if not re.match(r"^[a-z][a-z0-9_]*$", feat):
                continue
            feats[feat] = feats.get(feat, 0) + 1
        for feat, _cnt in sorted(feats.items(), key=lambda x: x[1], reverse=True)[:10]:
            mid = f"biz/{feat}"
            add_mapping(
                id=f"biz.{feat}",
                kind="glob",
                value=f"src/main/java/**/petclinic/{feat}/**",
                modules=[mid],
                summary=f"业务模块（弱先验，由 HTTP 入口推断）：{feat}",
                priority=90,
                source_ref="inferred:java_http_routes",
                source_kind="other",
            )

    _infer_biz_mappings_from_java_apis()

    filetree = {"kind": "view.filetree", "version": "0.1", "roots": [src_root], "mappings": mappings}
    write_json(root / "views/filetree.json", filetree)

    # entrypoints：MVP 只保留占位（让用户手工改命令名）
    ep_path = root / "views/entrypoints.json"
    if not ep_path.exists():
        write_json(root / "views/entrypoints.json", default_views_entrypoints("app"))

    # ===== Phase 2：构建系统 / 框架 extractor（v1~v2）=====
    cmake_res = extract_cmake(cwd)
    maven_res = extract_maven(cwd)
    spring_res = extract_spring(cwd)

    # 生成/更新 entrypoints view：合并默认 CLI 占位 + (CMake/Spring) entrypoints
    entrypoints_obj = default_views_entrypoints("app")
    # 直接组装：保持默认 + 追加
    entrypoints_obj["entrypoints"] = entrypoints_obj.get("entrypoints", [])
    existing_ids = {e.get("id") for e in entrypoints_obj["entrypoints"] if isinstance(e, dict)}
    for e in cmake_res.entrypoints:
        if e.id in existing_ids:
            continue
        entrypoints_obj["entrypoints"].append(e.to_view_obj())
        existing_ids.add(e.id)
    for e in spring_res.entrypoints:
        if e.id in existing_ids:
            continue
        entrypoints_obj["entrypoints"].append(e.to_view_obj())
        existing_ids.add(e.id)
    write_json(root / "views/entrypoints.json", entrypoints_obj)

    # 供后续 scan 阶段多次回写（flows/playbooks/relations 更新需要）
    idx_path = root / "L1/index.json"

    def _collect_module_ids(_mappings: list[dict[str, Any]]) -> set[str]:
        mids: set[str] = set()
        for m in _mappings:
            for mid in (m.get("targets", {}) or {}).get("modules", []) or []:
                if isinstance(mid, str) and mid:
                    mids.add(mid)
        mids.add("core")
        # entrypoints 默认包含 cli.default -> cli，为保持结构一致性，确保 cli 模块存在
        mids.add("cli")
        # 追加 CMake/Maven target modules（Phase 2）
        for mid in cmake_res.extra_modules:
            mids.add(mid)
        for mid in maven_res.extra_modules:
            mids.add(mid)
        return mids

    def _write_index(module_ids: set[str], relations_ids: list[str] | None = None) -> None:
        idx = _read_json(idx_path) if idx_path.exists() else default_l1_index()
        if not isinstance(idx, dict):
            idx = default_l1_index()
        idx["kind"] = "index"
        idx["version"] = idx.get("version") or "0.1"
        idx["modules"] = sorted(module_ids)
        idx["apis"] = sorted({a["id"] for a in (java_apis + cpp_apis) if isinstance(a, dict) and a.get("id")})
        idx["flows"] = idx.get("flows") or []
        idx["playbooks"] = idx.get("playbooks") or []
        if relations_ids is not None:
            idx["relations"] = relations_ids
        else:
            idx["relations"] = idx.get("relations") or []
        write_json(idx_path, idx)

    module_ids = _collect_module_ids(mappings)
    _write_index(module_ids)

    # views/module_files：module -> files（用于“模块归属硬化”与可审计）
    jr_obj: dict[str, Any] | None = None
    mf: dict[str, Any] | None = None
    ms: dict[str, Any] | None = None

    def _gen_views_from_filetree() -> None:
        nonlocal mf, ms, jr_obj
        jr_obj = None
        mf = _generate_module_files_view(
            cwd=cwd,
            src_root=src_root,
            filetree=filetree,
            module_ids=module_ids,
            ignore=list(cfg.ignore or []),
        )
        write_json(root / "views/module_files.json", mf)
        ms = _generate_module_symbols_view(
            cwd=cwd,
            src_root=src_root,
            module_files_view=mf,
        )
        write_json(root / "views/module_symbols.json", ms)
        if java_apis:
            jr_obj = _generate_java_http_routes_view(cwd=cwd, java_apis=java_apis, module_symbols_view=ms)
            write_json(root / "views/java_http_routes.json", jr_obj)

        # 全仓符号索引（JSONL）
        try:
            recs = _generate_symbol_index_jsonl(
                cwd=cwd, module_files_view=mf, roots=list(cfg.roots or ["src"]), ignore=list(cfg.ignore or [])
            )
            (root / "views").mkdir(parents=True, exist_ok=True)
            out = root / "views/symbol_index.jsonl"
            with out.open("w", encoding="utf-8") as f:
                for r in recs:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except Exception as si_e:
            (root / "views/symbol_index.error.txt").write_text(str(si_e), encoding="utf-8")

    try:
        _gen_views_from_filetree()
    except Exception as e:
        (root / "views/module_files.error.txt").write_text(str(e), encoding="utf-8")

    # 生成入口遍历图（v1：Java 用 routes/DI，C++ 用 public headers->module）
    try:
        if jr_obj:
            eg = _generate_entry_graph_view(java_http_routes_view=jr_obj)
        elif cpp_apis:
            eg = _generate_entry_graph_view_cpp(cpp_apis=cpp_apis)
        else:
            eg = None
        if eg is not None:
            write_json(root / "views/entry_graph.json", eg)
            try:
                (root / "views/entry_graph.error.txt").unlink(missing_ok=True)
            except Exception:
                pass
    except Exception as eg_e:
        (root / "views/entry_graph.error.txt").write_text(str(eg_e), encoding="utf-8")

    # ===== 全自动治理（LLM）：重写 filetree 并再生成一次关键 views =====
    def _llm_enabled() -> bool:
        if cfg.auto_partition_filetree is False:
            return False
        try:
            load_openai_config()
            return True if (cfg.auto_partition_filetree is None) else bool(cfg.auto_partition_filetree)
        except Exception:
            return False

    # LLM 重写 filetree：Java 依赖 routes；C++ 依赖 cpp_apis（public headers）
    if _llm_enabled() and ((jr_obj is not None) or (cpp_apis is not None and len(cpp_apis) > 0)) and ms and mf:
        try:
            new_filetree = _llm_rewrite_filetree_view(
                cwd=cwd,
                filetree_current=filetree,
                module_symbols_view=ms,
                module_files_view=mf,
                java_http_routes_view=jr_obj,
                cpp_apis=cpp_apis,
                entrypoints_view=entrypoints_obj,
            )
            # 覆盖 filetree，并据此刷新 mappings/module_ids/index
            filetree = new_filetree
            mappings = list(new_filetree.get("mappings") or [])
            write_json(root / "views/filetree.json", filetree)
            module_ids = _collect_module_ids(mappings)
            _write_index(module_ids)

            # 重新生成 views（与新 filetree 对齐）
            _gen_views_from_filetree()
            # entry_graph 也刷新（保持与新 filetree 对齐的 module 标注）
            try:
                if jr_obj:
                    eg = _generate_entry_graph_view(java_http_routes_view=jr_obj)
                elif cpp_apis:
                    eg = _generate_entry_graph_view_cpp(cpp_apis=cpp_apis)
                else:
                    eg = None
                if eg is not None:
                    write_json(root / "views/entry_graph.json", eg)
            except Exception:
                pass
            # 成功后清理旧错误文件（避免误导）
            try:
                (root / "views/filetree.llm.error.txt").unlink(missing_ok=True)
            except Exception:
                pass
        except Exception as llm_e:
            (root / "views/filetree.llm.error.txt").write_text(str(llm_e), encoding="utf-8")

    # 写入 L1/apis（尽量不覆盖人工编辑；但允许对“自动生成且引用不存在模块”的条目做最小修正）
    api_schema = load_embedded_schema("api.schema.json")
    api_dir = root / "L1/apis"
    api_dir.mkdir(parents=True, exist_ok=True)
    for api in (java_apis + cpp_apis):
        if not isinstance(api, dict) or not api.get("id"):
            continue
        api_id = str(api["id"])
        p = api_dir / Path(api_id + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            existing = _read_json(p)
            if isinstance(existing, dict):
                # 判断是否为自动生成：confidence=low 且 provenance.sources 包含 file
                prov2 = existing.get("provenance") or {}
                sources2 = (prov2.get("sources") or []) if isinstance(prov2, dict) else []
                is_auto = (
                    isinstance(prov2, dict)
                    and str(prov2.get("confidence") or "") == "low"
                    and any(isinstance(s, dict) and s.get("kind") == "file" for s in sources2)
                )
                if is_auto:
                    # 最小修正：related_modules 里若引用了不存在模块，则用最新抽取结果覆盖（仅该字段）
                    cur_rm = existing.get("related_modules") or []
                    if isinstance(cur_rm, list) and any((m not in module_ids) for m in cur_rm if isinstance(m, str)):
                        new_rm = api.get("related_modules") or []
                        if isinstance(new_rm, list):
                            # 过滤到存在模块；若全被过滤则回退到 core
                            filtered = [m for m in new_rm if isinstance(m, str) and m in module_ids]
                            existing["related_modules"] = filtered or ["core"]
                    # 刷新 provenance commit
                    if isinstance(prov2, dict):
                        prov2["last_verified_commit"] = commit
                        existing["provenance"] = prov2
                    write_json(p, existing)
            continue

        # 新建：填 provenance commit
        prov = api.get("provenance") or {}
        if isinstance(prov, dict):
            prov["last_verified_commit"] = commit
            api["provenance"] = prov
        write_json(p, api)

        # L2 模板（不覆盖）
        l2 = root / "L2/apis" / Path(api_id + ".md")
        l2.parent.mkdir(parents=True, exist_ok=True)
        if not l2.exists():
            l2.write_text(
                (
                    f"# {api_id}\n\n"
                    "## 用途\n\n- （待补充）\n\n"
                    "## 约束\n\n- （待补充：权限/鉴权/副作用）\n\n"
                    "## 示例\n\n- （待补充）\n"
                ),
                encoding="utf-8",
            )

    # 生成最小 flows/playbooks（v1：保证非空且可逐步细化）
    flows: list[dict[str, Any]] = []
    playbooks: list[dict[str, Any]] = []

    if java_apis:
        # 尝试生成“有证据的”Spring 注入链 flow（v1：regex/启发式）
        stages: list[dict[str, Any]] = []
        confidence = "low"
        try:
            ms_path = root / "views/module_symbols.json"
            ms = _read_json(ms_path) if ms_path.exists() else {}
            edges = _extract_spring_injection_edges(cwd=cwd, module_symbols_view=ms)
            if edges:
                confidence = "medium"
                controllers = {}
                services = {}
                repos = {}
                others = {}
                for e in edges:
                    controllers.setdefault(e["from_module"], []).append(e)
                    st = e.get("to_stereotype") or "other"
                    if st == "service":
                        services.setdefault(e.get("to_module") or "app/java", []).append(e)
                    elif st == "repository":
                        repos.setdefault(e.get("to_module") or "app/java", []).append(e)
                    else:
                        others.setdefault(e.get("to_module") or "app/java", []).append(e)

                def _mk_stage(sid: str, title: str, bucket: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
                    mods = sorted(bucket.keys())
                    ev: list[dict[str, Any]] = []
                    for _m, items in bucket.items():
                        for it in items[:20]:
                            ev.extend(it.get("evidence") or [])
                    return {"id": sid, "summary": title, "modules": mods or ["app/java"], "apis": [], "evidence": ev}

                stages.append(
                    {
                        "id": "controller",
                        "summary": "HTTP Controller（从路由/handler 出发）",
                        "modules": sorted(controllers.keys()) or ["app/java"],
                        "apis": [a["id"] for a in java_apis[:5]],
                        "evidence": [it.get("evidence")[0] for items in controllers.values() for it in items[:20] if it.get("evidence")],
                    }
                )
                if services:
                    stages.append(_mk_stage("service", "Service（由注入关系推断）", services))
                if repos:
                    stages.append(_mk_stage("repository", "Repository（由注入关系推断）", repos))
                if others:
                    stages.append(_mk_stage("other", "Other dependencies（待进一步语义化）", others))
        except Exception:
            stages = []

        if not stages:
            stages = [
                {"id": "controller", "summary": "HTTP Controller", "modules": ["app/java"], "apis": [java_apis[0]["id"]]},
                {"id": "domain", "summary": "Domain/Service/Repository（待细化）", "modules": ["app/java"], "apis": []},
            ]

        flows.append(
            {
                "kind": "flow",
                "id": "flow/java/http-request",
                "name": "Java HTTP 请求处理链路",
                "summary": "从 Controller 出发，基于注入/类型引用关系推断的协作链路（含证据）。",
                "stages": stages,
                "provenance": {"sources": [{"kind": "command", "ref": "aise scan"}], "last_verified_commit": commit, "confidence": confidence},
            }
        )
        playbooks.extend(
            [
                {
                    "kind": "playbook",
                    "id": "playbook/java/add-endpoint",
                    "name": "新增 REST Endpoint（Java/Spring）",
                    "summary": "在 Spring 项目中新增一个 REST 路由，并补齐测试与文档。",
                    "steps": [
                        "1) 在 Controller 中新增路由方法（@GetMapping/@PostMapping 等）",
                        "2) 若涉及业务逻辑，新增/修改 Service 层方法",
                        "3) 若涉及持久化，更新 Repository/DB 迁移（如有）",
                        "4) 新增或更新测试（src/test/java）",
                        "5) 更新 CodeWiki：补齐对应模块 L1/L2、apis 条目（必要时运行 aise scan）",
                    ],
                    "verifications": ["mvn test"],
                    "related_modules": ["app/java", "test/java", "build/maven"],
                    "provenance": {"sources": [{"kind": "command", "ref": "aise scan"}], "last_verified_commit": commit, "confidence": "low"},
                },
                {
                    "kind": "playbook",
                    "id": "playbook/java/add-test",
                    "name": "新增测试（Java）",
                    "summary": "为新增/修改的功能补齐单元/集成测试，并确保 CI 通过。",
                    "steps": [
                        "1) 在 src/test/java 下新增测试类/用例",
                        "2) 若需要测试数据，准备 fixtures 或 mock",
                        "3) 运行 mvn test 并修复失败",
                        "4) 更新 CodeWiki：记录测试入口与注意事项",
                    ],
                    "verifications": ["mvn test"],
                    "related_modules": ["test/java", "build/maven"],
                    "provenance": {"sources": [{"kind": "command", "ref": "aise scan"}], "last_verified_commit": commit, "confidence": "low"},
                },
            ]
        )

    if cpp_apis:
        flows.append(
            {
                "kind": "flow",
                "id": "flow/cpp/build-and-test",
                "name": "CMake 构建与测试链路（粗粒度）",
                "summary": "从 CMake configure/build 到 CTest 执行（v1 结构占位）。",
                "stages": [
                    {"id": "configure", "summary": "CMake configure", "modules": ["build/cmake"], "apis": []},
                    {"id": "build", "summary": "build targets", "modules": ["build/cmake"], "apis": []},
                    {"id": "test", "summary": "CTest / unit tests", "modules": ["cpp/googletest"], "apis": []},
                ],
                "provenance": {"sources": [{"kind": "command", "ref": "aise scan"}], "last_verified_commit": commit, "confidence": "low"},
            }
        )
        playbooks.extend(
            [
                {
                    "kind": "playbook",
                    "id": "playbook/cpp/add-test",
                    "name": "新增测试（C++/googletest）",
                    "summary": "在 googletest 项目或使用 googletest 的项目中新增一个单元测试。",
                    "steps": [
                        "1) 在 tests/ 或对应目录新增 *_test.cc",
                        "2) 在 CMakeLists.txt 中把新测试源文件加入 target（或新增 target）",
                        "3) 运行 ctest 并修复失败",
                        "4) 更新 CodeWiki：记录测试入口与注意事项",
                    ],
                    "verifications": ["ctest"],
                    "related_modules": ["build/cmake", "cpp/googletest"],
                    "provenance": {"sources": [{"kind": "command", "ref": "aise scan"}], "last_verified_commit": commit, "confidence": "low"},
                },
                {
                    "kind": "playbook",
                    "id": "playbook/cpp/add-target",
                    "name": "新增 CMake Target（C++）",
                    "summary": "新增一个 library/executable target，并正确声明依赖与 include。",
                    "steps": [
                        "1) 在 CMakeLists.txt 添加 add_library/add_executable",
                        "2) 使用 target_link_libraries 声明依赖",
                        "3) 使用 target_include_directories 声明 include 路径",
                        "4) 运行 cmake build 与 ctest",
                    ],
                    "verifications": ["ctest"],
                    "related_modules": ["build/cmake"],
                    "provenance": {"sources": [{"kind": "command", "ref": "aise scan"}], "last_verified_commit": commit, "confidence": "low"},
                },
            ]
        )

    # 写入 flows/playbooks（不覆盖）
    # 资产 A：写入 L1/relations（允许对自动生成低置信版本做回填升级）
    relations_ids: list[str] = []
    rel_dir = root / "L1/relations"
    rel_dir.mkdir(parents=True, exist_ok=True)
    if jr_obj:
        rel = _generate_java_http_di_relations(jr_view=jr_obj, commit=commit)
        if rel:
            rid = str(rel["id"])
            rp = rel_dir / Path(rid + ".json")
            rp.parent.mkdir(parents=True, exist_ok=True)
            should_write = not rp.exists()
            if rp.exists():
                try:
                    old = _read_json(rp)
                    prov = (old or {}).get("provenance") if isinstance(old, dict) else {}
                    conf = str((prov or {}).get("confidence") or "")
                    sources = (prov or {}).get("sources") if isinstance(prov, dict) else []
                    is_auto = any(
                        isinstance(s, dict) and s.get("kind") == "command" and s.get("ref") == "aise scan"
                        for s in (sources or [])
                    )
                    if is_auto and conf in ("low", ""):
                        should_write = True
                except Exception:
                    pass
            if should_write:
                write_json(rp, rel)
            relations_ids.append(rid)

    flow_dir = root / "L1/flows"
    pb_dir = root / "L1/playbooks"
    flow_dir.mkdir(parents=True, exist_ok=True)
    pb_dir.mkdir(parents=True, exist_ok=True)
    for fl in flows:
        fid = fl["id"]
        fp = flow_dir / Path(fid + ".json")
        fp.parent.mkdir(parents=True, exist_ok=True)
        should_write = not fp.exists()
        if fp.exists() and fid == "flow/java/http-request":
            # 允许回填：仅对“自动生成且低置信”的占位 flow 做升级（避免覆盖人工编辑）
            try:
                old = _read_json(fp)
                prov = (old or {}).get("provenance") if isinstance(old, dict) else {}
                conf = str((prov or {}).get("confidence") or "")
                sources = (prov or {}).get("sources") if isinstance(prov, dict) else []
                is_auto = any(isinstance(s, dict) and s.get("kind") == "command" and s.get("ref") == "aise scan" for s in (sources or []))
                if is_auto and conf in ("low", ""):
                    should_write = True
            except Exception:
                pass
        if should_write:
            write_json(fp, fl)
            l2 = root / "L2/flows" / Path(fid + ".md")
            l2.parent.mkdir(parents=True, exist_ok=True)
            if not l2.exists():
                l2.write_text(f"# {fid}\n\n## 说明\n\n- （待补充）\n", encoding="utf-8")
    for pb in playbooks:
        pid = pb["id"]
        pp = pb_dir / Path(pid + ".json")
        pp.parent.mkdir(parents=True, exist_ok=True)
        if not pp.exists():
            write_json(pp, pb)
            l2 = root / "L2/playbooks" / Path(pid + ".md")
            l2.parent.mkdir(parents=True, exist_ok=True)
            if not l2.exists():
                l2.write_text(f"# {pid}\n\n## 说明\n\n- （待补充）\n", encoding="utf-8")

    # 回写 index 引用
    idx = _read_json(idx_path)
    if isinstance(idx, dict):
        idx["flows"] = [f["id"] for f in flows]
        idx["playbooks"] = [p["id"] for p in playbooks]
        idx["relations"] = relations_ids
        write_json(idx_path, idx)

    # 生成/刷新 module L1 文件（不覆盖人工编辑，仅补缺+刷新 commit）
    # depends_on 填充仅用于“新建模块”（不覆盖人工编辑）
    depends_map: dict[str, list[str]] = {}
    depends_map.update(cmake_res.module_depends_on or {})
    depends_map.update(maven_res.module_depends_on or {})

    for mid in sorted(module_ids):
        _ensure_module_l1(
            root,
            mid,
            commit=commit,
            sources=[{"kind": "command", "ref": "aise scan", "note": "启发式生成"}],
            depends_on=depends_map.get(mid),
        )

        # L2 模板骨架（不覆盖人工编辑）
        l2_path = root / "L2/modules" / Path(mid + ".md")
        l2_path.parent.mkdir(parents=True, exist_ok=True)
        if not l2_path.exists():
            l2_path.write_text(
                (
                    f"# {mid}\n\n"
                    "## 设计意图\n\n"
                    "- （待补充）\n\n"
                    "## 隐含假设\n\n"
                    "- （待补充）\n\n"
                    "## 修改指南\n\n"
                    "- （待补充：常见改动步骤/风险点/需要跑的测试）\n"
                ),
                encoding="utf-8",
            )


def _iter_filetree_rules(filetree: dict[str, Any]) -> list[tuple[int, MatchRule, dict[str, Any]]]:
    rules: list[tuple[int, MatchRule, dict[str, Any]]] = []
    for m in filetree.get("mappings", []):
        prio = int(m.get("priority", 0) or 0)
        match = m["match"]
        rule = MatchRule(kind=match["kind"], value=match["value"])
        rules.append((prio, rule, m))
    rules.sort(key=lambda x: x[0], reverse=True)
    return rules


def _map_files_to_modules(filetree: dict[str, Any], files: Iterable[str]) -> set[str]:
    rules = _iter_filetree_rules(filetree)
    out: set[str] = set()
    for f in files:
        # best-match：按 priority 只取第一条命中的规则（可让归属更确定）
        for _prio, rule, mapping in rules:
            if rule.matches(f):
                for mid in mapping["targets"].get("modules", []) or []:
                    out.add(mid)
                break
    return out


def _list_repo_files(cwd: Path, root_rel: str, ignore: list[str]) -> list[str]:
    """
    列出仓库文件（相对 cwd 的 POSIX 路径），用于生成 module_files 视图。
    """
    root = (cwd / root_rel).resolve()
    out: list[str] = []

    def _ignored(rel: str) -> bool:
        # 简易 glob ignore（与 validate/diff gate 保持一致风格）
        import fnmatch

        p = rel.replace("\\", "/")
        for pat in ignore:
            if fnmatch.fnmatch(p, pat):
                return True
        return False

    if not root.exists():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(cwd).as_posix()
        # 永远忽略 docs/codewiki 自己，避免自引用膨胀
        if rel.startswith("docs/codewiki/"):
            continue
        if _ignored(rel):
            continue
        out.append(rel)
    out.sort()
    return out


def _generate_module_files_view(
    *,
    cwd: Path,
    src_root: str,
    filetree: dict[str, Any],
    module_ids: set[str],
    ignore: list[str],
    max_files_per_module: int = 2000,
    max_unmapped_samples: int = 200,
) -> dict[str, Any]:
    """
    生成 views/module_files.json（module → files[] + 统计）。
    """
    files = _list_repo_files(cwd, src_root, ignore=ignore)
    rules = _iter_filetree_rules(filetree)

    modules: dict[str, dict[str, Any]] = {}
    unmapped: list[str] = []

    def _bucket(mid: str) -> dict[str, Any]:
        if mid not in modules:
            modules[mid] = {"count": 0, "files_truncated": False, "files": [], "counts_by_ext": {}}
        return modules[mid]

    for f in files:
        matched_mapping = None
        for _prio, rule, mapping in rules:
            if rule.matches(f):
                matched_mapping = mapping
                break

        if not matched_mapping:
            # 未命中任何规则：暂时记为 unmapped（不强行塞 core，便于发现缺口）
            if len(unmapped) < max_unmapped_samples:
                unmapped.append(f)
            continue

        mids = matched_mapping.get("targets", {}).get("modules", []) or []
        if not mids:
            if len(unmapped) < max_unmapped_samples:
                unmapped.append(f)
            continue

        for mid in mids:
            if not isinstance(mid, str) or not mid:
                continue
            # module_ids 里没有的模块也记录（便于 validate 发现），但仍然产出视图
            b = _bucket(mid)
            b["count"] += 1
            ext = Path(f).suffix.lower() or "<none>"
            b["counts_by_ext"][ext] = int(b["counts_by_ext"].get(ext, 0)) + 1
            if not b["files_truncated"]:
                b["files"].append(f)
                if len(b["files"]) > max_files_per_module:
                    b["files"] = b["files"][:max_files_per_module]
                    b["files_truncated"] = True

    # 确保所有 module_ids 至少有空桶（便于 UI/导出一致）
    for mid in sorted(module_ids):
        _bucket(mid)

    return {
        "kind": "view.module_files",
        "version": "0.1",
        "root": src_root,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_files": len(files),
        "unmapped_count": len(unmapped),
        "modules": modules,
        "unmapped_files": unmapped,
    }


def _guess_java_stereotype(text: str) -> str:
    # 按常见 Spring stereotype/领域标注做弱推断（v1）
    if "@RestController" in text or "@Controller" in text:
        return "controller"
    if "@Service" in text:
        return "service"
    if "@Repository" in text:
        return "repository"
    if "@Component" in text:
        return "component"
    if "@Entity" in text:
        return "entity"
    # 常见 Spring Data：接口名/父接口包含 Repository
    if re.search(r"\binterface\s+\w*Repository\b", text) and re.search(r"\bextends\s+\w*Repository\b", text):
        return "repository"
    if re.search(r"\bextends\s+(CrudRepository|JpaRepository|PagingAndSortingRepository)\b", text):
        return "repository"
    if "@Test" in text or "org.junit" in text:
        return "test"
    return "other"


def _build_class_index(module_symbols_view: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """
    simpleName -> [{module, package, file, stereotype}]
    """
    idx: dict[str, list[dict[str, Any]]] = {}
    mods = module_symbols_view.get("modules") if isinstance(module_symbols_view, dict) else None
    if not isinstance(mods, dict):
        return idx
    for mid, info in mods.items():
        if not isinstance(mid, str) or not isinstance(info, dict):
            continue
        for c in info.get("classes") or []:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not isinstance(name, str) or not name:
                continue
            rec = {
                "name": name,
                "module": mid,
                "package": c.get("package"),
                "file": c.get("file"),
                "stereotype": c.get("stereotype", "other"),
            }
            idx.setdefault(name, []).append(rec)
    return idx


def _extract_spring_injection_edges(
    *,
    cwd: Path,
    module_symbols_view: dict[str, Any],
    max_edges: int = 5000,
) -> list[dict[str, Any]]:
    """
    v1：仅抽取 Spring Controller -> (Service/Repository/Other) 的“注入边”。
    输出边：{from_class, from_file, to_type, to_module?, to_file?, kind="inject", evidence:[sourceRef]}
    """
    edges: list[dict[str, Any]] = []
    mods = module_symbols_view.get("modules") if isinstance(module_symbols_view, dict) else None
    if not isinstance(mods, dict):
        return edges

    class_index = _build_class_index(module_symbols_view)

    def _pick_type(name: str, prefer_pkg: str | None) -> dict[str, Any] | None:
        cands = class_index.get(name) or []
        if not cands:
            return None
        if prefer_pkg:
            for c in cands:
                if c.get("package") == prefer_pkg:
                    return c
        return cands[0]

    # regex：字段注入/构造器注入（简化）
    field_re = re.compile(r"(?m)^\s*(private|protected|public)\s+(final\s+)?([A-Za-z_][A-Za-z0-9_<>.]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;")
    ctor_re = re.compile(r"\bpublic\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)")
    param_type_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_<>.]*)\s+[A-Za-z_][A-Za-z0-9_]*\b")

    for mid, info in mods.items():
        if not isinstance(info, dict):
            continue
        for c in info.get("classes") or []:
            if not isinstance(c, dict):
                continue
            if c.get("stereotype") != "controller":
                continue
            cls = str(c.get("name") or "")
            f = str(c.get("file") or "")
            pkg = c.get("package")
            if not cls or not f:
                continue
            p = cwd / f
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()

            injected_types: list[tuple[str, int, str]] = []  # (TypeName, lineNo, note)
            # 字段注入：简单识别 @Autowired 的下一条字段声明（或同一段落内字段）
            for i, line in enumerate(lines, start=1):
                if "@Autowired" in line:
                    # lookahead 3 lines
                    for j in range(i, min(i + 4, len(lines)) + 1):
                        m = field_re.match(lines[j - 1])
                        if m:
                            t = m.group(3)
                            t2 = re.sub(r"<.*?>", "", t).split(".")[-1]
                            injected_types.append((t2, j, "@Autowired field"))
                            break

            # 构造器注入：匹配 public ClassName(...)
            for m in ctor_re.finditer(text):
                ctor_name = m.group(1)
                if ctor_name != cls:
                    continue
                params = m.group(2) or ""
                for pm in param_type_re.finditer(params):
                    t = pm.group(1)
                    t2 = re.sub(r"<.*?>", "", t).split(".")[-1]
                    # line number：粗略取构造器起始行
                    line_no = text[: m.start()].count("\n") + 1
                    injected_types.append((t2, line_no, "constructor param"))

            for tname, line_no, note in injected_types:
                tgt = _pick_type(tname, prefer_pkg=pkg if isinstance(pkg, str) else None)
                edge = {
                    "kind": "inject",
                    "from_class": cls,
                    "from_file": f,
                    "from_module": mid,
                    "to_type": tname,
                    "to_module": (tgt or {}).get("module"),
                    "to_file": (tgt or {}).get("file"),
                    "to_stereotype": (tgt or {}).get("stereotype"),
                    "evidence": [{"kind": "file", "ref": f"{f}:{line_no}", "note": note}],
                }
                edges.append(edge)
                if len(edges) >= max_edges:
                    return edges
    return edges


def _class_from_handler_symbol(symbol: str | None, handler_file: str | None) -> str | None:
    if symbol and isinstance(symbol, str):
        # 例：src.main.java.org.springframework....OwnerController
        return symbol.split(".")[-1] or None
    if handler_file and isinstance(handler_file, str):
        base = Path(handler_file).name
        if base.endswith(".java"):
            return base[:-5]
        return base
    return None


def _pick_class_record(
    *,
    class_index: dict[str, list[dict[str, Any]]],
    name: str,
    prefer_file: str | None = None,
    prefer_pkg: str | None = None,
) -> dict[str, Any] | None:
    cands = class_index.get(name) or []
    if not cands:
        return None
    if prefer_file:
        for c in cands:
            if c.get("file") == prefer_file:
                return c
    if prefer_pkg:
        for c in cands:
            if c.get("package") == prefer_pkg:
                return c
    return cands[0]


def _extract_injected_types_for_class(
    *, cwd: Path, class_name: str, file_rel: str
) -> list[tuple[str, dict[str, Any]]]:
    """
    返回 [(TypeSimpleName, evidenceSourceRef)]
    """
    p = cwd / file_rel
    if not p.exists():
        return []
    text = p.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    field_re = re.compile(r"(?m)^\s*(private|protected|public)\s+(final\s+)?([A-Za-z_][A-Za-z0-9_<>.]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;")
    ctor_re = re.compile(rf"\bpublic\s+{re.escape(class_name)}\s*\(([^)]*)\)")
    param_type_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_<>.]*)\s+[A-Za-z_][A-Za-z0-9_]*\b")

    injected: list[tuple[str, dict[str, Any]]] = []

    # @Autowired field (lookahead)
    for i, line in enumerate(lines, start=1):
        if "@Autowired" in line:
            for j in range(i, min(i + 4, len(lines)) + 1):
                m = field_re.match(lines[j - 1])
                if m:
                    t = m.group(3)
                    t2 = re.sub(r"<.*?>", "", t).split(".")[-1]
                    injected.append((t2, {"kind": "file", "ref": f"{file_rel}:{j}", "note": "@Autowired field"}))
                    break

    # constructor params
    for m in ctor_re.finditer(text):
        params = m.group(1) or ""
        for pm in param_type_re.finditer(params):
            t = pm.group(1)
            t2 = re.sub(r"<.*?>", "", t).split(".")[-1]
            line_no = text[: m.start()].count("\n") + 1
            injected.append((t2, {"kind": "file", "ref": f"{file_rel}:{line_no}", "note": "constructor param"}))

    return injected


def _generate_java_http_routes_view(
    *,
    cwd: Path,
    java_apis: list[dict[str, Any]],
    module_symbols_view: dict[str, Any],
    max_routes: int = 2000,
) -> dict[str, Any]:
    """
    J2：route -> handler -> DI chain（controller -> service -> repository）
    - 以 module_symbols 为符号索引（class -> module/file/package/stereotype）
    - 以注入关系为主要扩展边（constructor/@Autowired）
    """
    class_index = _build_class_index(module_symbols_view)
    routes: list[dict[str, Any]] = []

    for api in java_apis[:max_routes]:
        http = api.get("http") if isinstance(api, dict) else None
        if not isinstance(http, dict):
            continue
        handler = http.get("handler")
        if not isinstance(handler, dict):
            continue
        handler_file = handler.get("file")
        handler_symbol = handler.get("symbol")
        cls_name = _class_from_handler_symbol(handler_symbol, handler_file)
        if not cls_name or not isinstance(handler_file, str):
            continue

        controller = _pick_class_record(class_index=class_index, name=cls_name, prefer_file=handler_file)
        if not controller:
            continue
        if not controller.get("module") or not controller.get("file"):
            continue

        chain: list[dict[str, Any]] = [
            {
                "role": "controller",
                "class": cls_name,
                "package": controller.get("package"),
                "stereotype": controller.get("stereotype"),
                "module": controller.get("module"),
                "file": controller.get("file"),
                "evidence": [{"kind": "file", "ref": handler_file, "note": "handler file"}],
            }
        ]

        injected = _extract_injected_types_for_class(cwd=cwd, class_name=cls_name, file_rel=handler_file)

        def _pick_by_role(role: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
            for tname, ev in injected:
                rec = _pick_class_record(
                    class_index=class_index, name=tname, prefer_pkg=controller.get("package")
                )
                if not rec:
                    continue
                st = rec.get("stereotype")
                if role == "service" and st == "service":
                    return rec, ev
                if role == "repository" and st == "repository":
                    return rec, ev
            return None, None

        # controller -> service (preferred)
        svc, svc_ev = _pick_by_role("service")
        if svc:
            if not svc.get("name"):
                continue
            chain.append(
                {
                    "role": "service",
                    "class": str(svc.get("name") or ""),
                    "package": svc.get("package"),
                    "stereotype": svc.get("stereotype"),
                    "module": svc.get("module"),
                    "file": svc.get("file"),
                    "evidence": [svc_ev] if svc_ev else [],
                }
            )
            # service -> repository (from service file)
            svc_cls = str(chain[-1]["class"])
            svc_inj = _extract_injected_types_for_class(cwd=cwd, class_name=svc_cls, file_rel=str(svc.get("file") or ""))
            for tname, ev in svc_inj:
                rec = _pick_class_record(class_index=class_index, name=tname, prefer_pkg=svc.get("package"))
                if rec and rec.get("stereotype") == "repository":
                    if rec.get("module") and rec.get("file") and rec.get("name"):
                        chain.append(
                            {
                                "role": "repository",
                                "class": str(rec.get("name") or tname),
                                "package": rec.get("package"),
                                "stereotype": rec.get("stereotype"),
                                "module": rec.get("module"),
                                "file": rec.get("file"),
                                "evidence": [ev],
                            }
                        )
                    break
        else:
            # controller 直接注入 repository（或其他）
            repo, repo_ev = _pick_by_role("repository")
            if repo:
                chain.append(
                    {
                        "role": "repository",
                        "class": str(repo.get("name") or ""),
                        "package": repo.get("package"),
                        "stereotype": repo.get("stereotype"),
                        "module": repo.get("module"),
                        "file": repo.get("file"),
                        "evidence": [repo_ev] if repo_ev else [],
                    }
                )

        routes.append(
            {
                "api_id": api.get("id"),
                "method": http.get("method"),
                "path": http.get("path"),
                "handler_file": handler_file,
                "handler_class": cls_name,
                "chain": chain,
            }
        )

    return {"kind": "view.java_http_routes", "version": "0.1", "generated_at": datetime.utcnow().isoformat() + "Z", "routes": routes}


def _generate_entry_graph_view(*, java_http_routes_view: dict[str, Any]) -> dict[str, Any]:
    """
    入口遍历图（v1，最小可用）：
    - route -> handler_class
    - handler_class -> next_class（inject，来自 DI chain 相邻节点）
    说明：这是“证据图”的第一步，后续可扩展到 callgraph/事件等边类型。
    """
    if not isinstance(java_http_routes_view, dict) or java_http_routes_view.get("kind") != "view.java_http_routes":
        raise ValueError("java_http_routes_view.kind 不是 view.java_http_routes")
    routes = java_http_routes_view.get("routes") or []
    if not isinstance(routes, list):
        raise ValueError("java_http_routes_view.routes 类型错误")

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def _ensure_node(_id: str, kind: str, label: str | None = None, module: str | None = None):
        if _id in nodes:
            return
        n: dict[str, Any] = {"id": _id, "kind": kind}
        if label:
            n["label"] = label
        if module:
            n["module"] = module
        nodes[_id] = n

    for r in routes:
        if not isinstance(r, dict):
            continue
        method = str(r.get("method") or "GET").upper()
        path = str(r.get("path") or "/")
        rid = f"route:{method} {path}"
        _ensure_node(rid, "route", label=f"{method} {path}")

        handler_class = str(r.get("handler_class") or "")
        if handler_class:
            cid = f"class:{handler_class}"
            _ensure_node(cid, "class", label=handler_class)
            ev = [{"kind": "file", "ref": str(r.get("handler_file") or ""), "note": "handler file"}]
            edges.append({"from": rid, "to": cid, "type": "route_to_handler", "evidence": ev})

        chain = r.get("chain") or []
        if not isinstance(chain, list) or len(chain) < 2:
            continue
        # chain nodes + inject edges
        for i in range(len(chain)):
            c = chain[i] if isinstance(chain[i], dict) else None
            if not c:
                continue
            cls = str(c.get("class") or "")
            mod = c.get("module")
            nid = f"class:{cls}" if cls else f"other:{i}"
            _ensure_node(nid, "class", label=cls or None, module=str(mod) if isinstance(mod, str) else None)
            if i < len(chain) - 1:
                nxt = chain[i + 1] if isinstance(chain[i + 1], dict) else None
                if not nxt:
                    continue
                cls2 = str(nxt.get("class") or "")
                mod2 = nxt.get("module")
                nid2 = f"class:{cls2}" if cls2 else f"other:{i+1}"
                _ensure_node(nid2, "class", label=cls2 or None, module=str(mod2) if isinstance(mod2, str) else None)
                ev2 = nxt.get("evidence") or []
                edges.append(
                    {
                        "from": nid,
                        "to": nid2,
                        "type": "inject",
                        "evidence": ev2 if isinstance(ev2, list) else [],
                    }
                )

    return {
        "kind": "view.entry_graph",
        "version": "0.1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def _generate_entry_graph_view_cpp(*, cpp_apis: list[dict[str, Any]]) -> dict[str, Any]:
    """
    C++ 入口遍历图（v1，最小可用）：
    - public header(api) -> related_module（来自 cpp_headers 提取的 related_modules）
    说明：先保证“入口资产”存在，后续再扩展到 target/link/include graph。
    """
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def _ensure_node(_id: str, kind: str, label: str | None = None, module: str | None = None):
        if _id in nodes:
            return
        n: dict[str, Any] = {"id": _id, "kind": kind}
        if label:
            n["label"] = label
        if module:
            n["module"] = module
        nodes[_id] = n

    for a in cpp_apis:
        if not isinstance(a, dict):
            continue
        api_id = str(a.get("id") or "")
        if not api_id:
            continue
        hdr = a.get("header") if isinstance(a.get("header"), dict) else {}
        f = hdr.get("file") if isinstance(hdr, dict) else None
        api_node = f"api:{api_id}"
        _ensure_node(api_node, "symbol", label=api_id)

        rms = a.get("related_modules") or []
        if not isinstance(rms, list):
            continue
        for m in rms[:6]:
            if not isinstance(m, str) or not m:
                continue
            mod_node = f"module:{m}"
            _ensure_node(mod_node, "module", label=m, module=m)
            ev = [{"kind": "file", "ref": str(f or ""), "note": "public header"}] if isinstance(f, str) else []
            edges.append({"from": api_node, "to": mod_node, "type": "other", "evidence": ev})

    return {
        "kind": "view.entry_graph",
        "version": "0.1",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def _generate_symbol_index_jsonl(
    *,
    cwd: Path,
    module_files_view: dict[str, Any],
    roots: list[str],
    ignore: list[str],
) -> list[dict[str, Any]]:
    """
    生成全仓符号索引（JSONL 逐行 record），目标：
    - 每个函数/方法/类/全局变量都能定位到 file + range（行列）
    - 每条记录标注 primary module（由 module_files 推导）
    """
    modules_obj = module_files_view.get("modules") if isinstance(module_files_view, dict) else None
    if not isinstance(modules_obj, dict):
        raise ValueError("module_files_view.modules 缺失或类型错误")

    file_to_module: dict[str, str] = {}
    for mid, m in modules_obj.items():
        if not isinstance(mid, str) or not isinstance(m, dict):
            continue
        for f in (m.get("files") or []):
            if isinstance(f, str) and f and f not in file_to_module:
                file_to_module[f] = mid

    # 也覆盖 unmapped_files（module 为空即可）
    for f in (module_files_view.get("unmapped_files") or []):
        if isinstance(f, str) and f and f not in file_to_module:
            file_to_module[f] = ""

    def _is_ignored(rel: str) -> bool:
        # ignore 是 glob 列表（来自 aise.yml），复用 path_match
        try:
            return any(match_glob(rel, pat) for pat in (ignore or []))
        except Exception:
            return False

    def _iter_source_files() -> list[str]:
        exts = {".java", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
        out: set[str] = set()
        for r in (roots or ["src"]):
            rp = cwd / r
            if not rp.exists() or not rp.is_dir():
                continue
            for p in rp.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in exts:
                    continue
                rel = str(p.relative_to(cwd)).replace("\\", "/")
                if _is_ignored(rel):
                    continue
                out.add(rel)
        # 兜底：把 module_files 出现过的文件也并进来（避免 roots 配置不全）
        for rel in file_to_module.keys():
            if _is_ignored(rel):
                continue
            out.add(rel)
        return sorted(out)

    records: list[dict[str, Any]] = []
    all_files = _iter_source_files()
    for rel in all_files:
        if _is_ignored(rel):
            continue
        p = cwd / rel
        if not p.exists() or not p.is_file():
            continue
        suffix = p.suffix.lower()
        try:
            if suffix == ".java":
                syms = extract_java_symbols(repo_root=cwd, file_path=p, rel_file=rel)
            elif suffix in (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"):
                syms = extract_cpp_symbols(repo_root=cwd, file_path=p, rel_file=rel)
            else:
                continue
            mod = file_to_module.get(rel) or None
            # 若该文件未抽到任何 symbol，也写入一个“文件级兜底记录”，保证可定位
            if not syms:
                try:
                    txt = p.read_text(encoding="utf-8", errors="ignore")
                    lines = txt.splitlines()
                    end_line = max(1, len(lines))
                    end_col = max(1, (len(lines[-1]) + 1) if lines else 1)
                except Exception:
                    end_line, end_col = 1, 1
                rec = {
                    "symbol_id": f"file:{hashlib.sha1(rel.encode('utf-8')).hexdigest()}",
                    "kind": "other",
                    "language": "java" if suffix == ".java" else "cpp",
                    "name": rel,
                    "file": rel,
                    "signature": "no_symbols",
                    "range": {"start_line": 1, "start_col": 1, "end_line": end_line, "end_col": end_col},
                }
                if mod:
                    rec["module"] = mod
                records.append(rec)
            else:
                for s in syms:
                    records.append(s.to_json(module=mod))
        except Exception:
            # 解析失败不阻断 scan；由 validate/统计阶段再做约束
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
                lines = txt.splitlines()
                end_line = max(1, len(lines))
                end_col = max(1, (len(lines[-1]) + 1) if lines else 1)
            except Exception:
                end_line, end_col = 1, 1
            records.append(
                (lambda _mod: (
                    {"symbol_id": f"file:{hashlib.sha1(rel.encode('utf-8')).hexdigest()}",
                     "kind": "other",
                     "language": "java" if suffix == ".java" else "cpp",
                     "name": rel,
                     "file": rel,
                     "signature": "parse_error",
                     "range": {"start_line": 1, "start_col": 1, "end_line": end_line, "end_col": end_col},
                     **({"module": _mod} if _mod else {})}
                ))(file_to_module.get(rel) or None)
            )
    return records


def _llm_rewrite_filetree_view(
    *,
    cwd: Path,
    filetree_current: dict[str, Any],
    module_symbols_view: dict[str, Any],
    module_files_view: dict[str, Any],
    java_http_routes_view: dict[str, Any] | None,
    cpp_apis: list[dict[str, Any]] | None,
    entrypoints_view: dict[str, Any],
) -> dict[str, Any]:
    """
    全自动治理：使用 LLM 生成新的 views/filetree.json（允许每次重写）。
    注意：LLM 只产出“规则”，membership 仍由工具根据规则计算（module_files）。
    """
    schema = load_embedded_schema("views-filetree.schema.json")
    validator = Draft202012Validator(schema)

    # 提供尽量短但有区分度的上下文（避免把全仓喂给模型）
    def _top_packages() -> list[tuple[str, int]]:
        mods = module_symbols_view.get("modules") if isinstance(module_symbols_view, dict) else {}
        pk: dict[str, int] = {}
        if isinstance(mods, dict):
            for m in mods.values():
                if not isinstance(m, dict):
                    continue
                for c in (m.get("classes") or [])[:200]:
                    if not isinstance(c, dict):
                        continue
                    p = c.get("package")
                    if isinstance(p, str) and p:
                        pk[p] = pk.get(p, 0) + 1
        return sorted(pk.items(), key=lambda x: x[1], reverse=True)[:30]

    routes_sample: list[dict[str, Any]] = []
    if isinstance(java_http_routes_view, dict):
        for r in (java_http_routes_view.get("routes") or [])[:30]:
            if not isinstance(r, dict):
                continue
            routes_sample.append(
                {
                    "method": r.get("method"),
                    "path": r.get("path"),
                    "handler_file": r.get("handler_file"),
                    "handler_class": r.get("handler_class"),
                    "chain": [
                        {"role": (n or {}).get("role"), "class": (n or {}).get("class"), "module": (n or {}).get("module")}
                        for n in ((r.get("chain") or [])[:4] if isinstance(r.get("chain"), list) else [])
                        if isinstance(n, dict)
                    ],
                }
            )

    cpp_headers_sample: list[dict[str, Any]] = []
    if isinstance(cpp_apis, list):
        for a in cpp_apis[:40]:
            if not isinstance(a, dict):
                continue
            hdr = a.get("header") if isinstance(a.get("header"), dict) else {}
            cpp_headers_sample.append(
                {
                    "id": a.get("id"),
                    "header_file": (hdr.get("file") if isinstance(hdr, dict) else None),
                    "related_modules": (a.get("related_modules") if isinstance(a.get("related_modules"), list) else []),
                }
            )

    ctx = {
        "entrypoints": (entrypoints_view.get("entrypoints") or [])[:30] if isinstance(entrypoints_view, dict) else [],
        "routes_sample": routes_sample,
        "cpp_headers_sample": cpp_headers_sample,
        "top_packages": _top_packages(),
        "current_filetree_mappings_sample": (filetree_current.get("mappings") or [])[:30] if isinstance(filetree_current, dict) else [],
        "schema_hint": {
            "kind": "view.filetree",
            "version": "0.1",
            "roots": ["src"],
            "mappings_item": {
                "id": "string",
                "match": {"kind": "glob", "value": "src/main/java/**/petclinic/owner/**"},
                "targets": {"modules": ["biz/owner"]},
                "summary": "string",
                "priority": 0,
                "source": {"kind": "other", "ref": "llm:auto_partition"},
            },
            "allowed_match_kind": ["glob", "regex", "prefix"]
        },
    }

    system = (
        "你是 codewiki agent 的结构化输出组件。你的任务是为仓库生成 views/filetree.json 的 mappings，"
        "以实现“稳定的模块边界”。要求：\n"
        "1) 只输出严格 JSON（不要 markdown）。\n"
        "2) 必须符合给定 schema_hint 的结构（kind/version/roots/mappings）。\n"
        "3) 模块 ID 允许层级：用 / 分层（例如 biz/owner 或 domain/owner 或 apps/web）。\n"
        "4) 每条 mapping 必须给出 summary 与 priority（建议 50~100）。\n"
        "5) match.kind 只能是：glob、regex、prefix（三选一）。禁止使用 literal/exact 等其他值。\n"
        "6) 不要编造不存在的路径；尽量用 glob 覆盖真实文件结构。\n"
    )
    user = (
        "请基于上下文生成新的 views/filetree.json（全量输出）。\n"
        "上下文：\n"
        + json.dumps(ctx, ensure_ascii=False, indent=2)
    )

    client = OpenAIClient(load_openai_config())
    try:
        resp = client.chat_completions(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    finally:
        client.close()

    content = (((resp.get("choices") or [{}])[0].get("message") or {}).get("content")) if isinstance(resp, dict) else ""
    if not isinstance(content, str):
        raise RuntimeError("LLM 返回缺少 message.content")
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        raise RuntimeError(f"LLM 未返回 JSON：{content[:200]}")
    data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise RuntimeError("LLM 输出非 object")

    # 兜底修复：部分 OpenAI-compat 模型会输出 match.kind=literal（schema 不支持）
    # 这里将 literal 视作“精确前缀匹配”，自动降级为 prefix。
    def _normalize_filetree(obj: dict[str, Any]) -> dict[str, Any]:
        maps = obj.get("mappings")
        if not isinstance(maps, list):
            return obj
        for it in maps:
            if not isinstance(it, dict):
                continue
            match = it.get("match")
            if not isinstance(match, dict):
                continue
            k = match.get("kind")
            if k == "literal":
                match["kind"] = "prefix"
        return obj

    data = _normalize_filetree(data)

    # 兜底：补齐 kind/version
    data.setdefault("kind", "view.filetree")
    data.setdefault("version", "0.1")
    if "roots" not in data and isinstance(filetree_current, dict):
        data["roots"] = filetree_current.get("roots") or ["src"]

    errs = list(validator.iter_errors(data))
    if errs:
        raise RuntimeError("LLM 生成的 filetree.json 未通过 schema 校验：" + "; ".join(e.message for e in errs[:3]))
    return data


def _generate_java_http_di_relations(
    *,
    jr_view: dict[str, Any],
    commit: str,
) -> dict[str, Any] | None:
    """
    资产 A：从 views/java_http_routes.json 聚合得到 module->module 关系图（inject）。
    """
    if not isinstance(jr_view, dict) or jr_view.get("kind") != "view.java_http_routes":
        return None
    routes = jr_view.get("routes") or []
    if not isinstance(routes, list) or not routes:
        return None

    edge_map: dict[tuple[str, str], dict[str, Any]] = {}

    def _add_edge(frm: str, to: str, ev: list[dict[str, Any]] | None):
        # 允许 frm == to：用于统计“模块内部协作链路密度”，同时也能在跨模块边为 0 时仍产出资产
        if not frm or not to:
            return
        k = (frm, to)
        if k not in edge_map:
            edge_map[k] = {"from_module": frm, "to_module": to, "type": "inject", "weight": 0, "evidence": []}
        edge_map[k]["weight"] += 1
        if ev:
            # 采样最多 5 条证据
            cur = edge_map[k].get("evidence") or []
            for x in ev:
                if len(cur) >= 5:
                    break
                if isinstance(x, dict):
                    cur.append(x)
            edge_map[k]["evidence"] = cur

    for r in routes:
        if not isinstance(r, dict):
            continue
        chain = r.get("chain") or []
        if not isinstance(chain, list) or len(chain) < 2:
            continue
        # 相邻节点形成边（controller->service, service->repo, controller->repo 等）
        for i in range(len(chain) - 1):
            a = chain[i] if isinstance(chain[i], dict) else None
            b = chain[i + 1] if isinstance(chain[i + 1], dict) else None
            if not a or not b:
                continue
            frm = a.get("module")
            to = b.get("module")
            ev = b.get("evidence") or []
            if isinstance(frm, str) and isinstance(to, str):
                _add_edge(frm, to, ev if isinstance(ev, list) else None)

    edges = list(edge_map.values())
    edges.sort(key=lambda e: int(e.get("weight", 0)), reverse=True)
    if not edges:
        return None

    return {
        "kind": "relations",
        "id": "relations/java/http-di",
        "name": "Java HTTP 注入链模块关系图",
        "summary": "从 HTTP 路由入口出发，基于 Spring 注入链（Controller→Service→Repository）聚合得到的模块关系边（带证据采样）。",
        "edges": edges,
        "provenance": {
            "sources": [{"kind": "command", "ref": "aise scan"}, {"kind": "file", "ref": "docs/codewiki/views/java_http_routes.json"}],
            "last_verified_commit": commit,
            "confidence": "medium",
        },
    }


def _extract_java_classes(java_text: str) -> tuple[str | None, list[dict[str, Any]]]:
    """
    极简 Java 解析（regex/启发式）：
    - 解析 package
    - 解析 class/interface/enum 名称
    - stereotype 通过注解关键词推断
    """
    pkg = None
    m = re.search(r"(?m)^\s*package\s+([a-zA-Z0-9_.]+)\s*;", java_text)
    if m:
        pkg = m.group(1)

    stereotype = _guess_java_stereotype(java_text)
    out: list[dict[str, Any]] = []
    for m2 in re.finditer(r"(?m)^\s*(public\s+)?(final\s+)?(class|interface|enum)\s+([A-Za-z_][A-Za-z0-9_]*)\b", java_text):
        out.append({"name": m2.group(4), "package": pkg, "stereotype": stereotype})
    return pkg, out


def _generate_module_symbols_view(
    *,
    cwd: Path,
    src_root: str,
    module_files_view: dict[str, Any],
    max_classes_per_module: int = 800,
) -> dict[str, Any]:
    """
    生成 views/module_symbols.json（module -> classes[]）。
    v1 仅抽取 Java 类（足够支持 Spring 注入链与模块语义化）。
    """
    modules_obj = module_files_view.get("modules") if isinstance(module_files_view, dict) else None
    if not isinstance(modules_obj, dict):
        raise ValueError("module_files_view.modules 缺失或类型错误")

    modules: dict[str, Any] = {}

    for mid, info in modules_obj.items():
        if not isinstance(mid, str) or not isinstance(info, dict):
            continue
        files = info.get("files") or []
        if not isinstance(files, list):
            continue
        classes: list[dict[str, Any]] = []
        truncated = False
        for rel in files:
            if not isinstance(rel, str):
                continue
            if not rel.endswith(".java"):
                continue
            p = cwd / rel
            if not p.exists():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            _, cls = _extract_java_classes(text)
            for c in cls:
                c["file"] = rel
                classes.append(c)
                if len(classes) >= max_classes_per_module:
                    truncated = True
                    break
            if truncated:
                break
        modules[mid] = {"classes_truncated": truncated, "classes": classes}

    return {
        "kind": "view.module_symbols",
        "version": "0.1",
        "root": src_root,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "modules": modules,
    }


def update_repo(cwd: Path, base: str, head: str) -> set[str]:
    """
    增量更新（MVP）：根据 git diff 计算受影响 moduleId，
    并尝试刷新这些 module 条目的 provenance.last_verified_commit（若存在）。
    返回受影响 module 集合（便于上层输出）。
    """
    ensure_git(cwd)
    root = cwd / CODEWIKI_DIR
    filetree_path = root / "views/filetree.json"
    if not filetree_path.exists():
        raise RuntimeError("缺少 docs/codewiki/views/filetree.json，请先运行 `aise init` 或 `aise scan`")

    files = changed_files(cwd, base, head)
    filetree = _read_json(filetree_path)
    affected = _map_files_to_modules(filetree, files)

    # 先保证受影响 modules 至少存在（缺失则补一个最小模板）
    for mid in sorted(affected):
        _ensure_module_l1(
            root,
            mid,
            commit=head,
            sources=[
                {"kind": "commit", "ref": f"{base}..{head}", "note": "增量更新触发"},
                {"kind": "command", "ref": "git diff --name-only base..head"},
            ],
        )

    # MVP：仅更新已存在 module 的 provenance（不生成新 module 内容）
    for mid in affected:
        mod_path = _module_path(root, mid)
        if not mod_path.exists():
            continue
        mod = _read_json(mod_path)
        if isinstance(mod, dict) and "provenance" in mod:
            mod["provenance"]["last_verified_commit"] = head
            write_json(mod_path, mod)

    return affected


def validate_views(cwd: Path) -> list[Finding]:
    root = cwd / CODEWIKI_DIR
    findings: list[Finding] = []
    cfg = load_config(cwd)

    # registry：用于解析 common.schema.json 的 $ref（避免网络检索）
    common = load_embedded_schema("common.schema.json")
    reg = Registry().with_resource(
        "common.schema.json", Resource.from_contents(common, default_specification=DRAFT202012)
    )
    if isinstance(common, dict) and isinstance(common.get("$id"), str) and common["$id"]:
        reg = reg.with_resource(common["$id"], Resource.from_contents(common, default_specification=DRAFT202012))

    # filetree schema
    filetree_path = root / "views/filetree.json"
    if filetree_path.exists():
        schema = load_embedded_schema("views-filetree.schema.json")
        data = _read_json(filetree_path)
        for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
            findings.append(
                Finding(
                    rule_id="R-VIEW-001",
                    severity="warn",
                    target=str(filetree_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"filetree.json schema 校验失败：{err.message}",
                )
            )

    # entrypoints schema
    ep_path = root / "views/entrypoints.json"
    if ep_path.exists():
        schema = load_embedded_schema("views-entrypoints.schema.json")
        data = _read_json(ep_path)
        for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
            findings.append(
                Finding(
                    rule_id="R-VIEW-002",
                    severity="warn",
                    target=str(ep_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"entrypoints.json schema 校验失败：{err.message}",
                )
            )

    # module_files schema
    mf_path = root / "views/module_files.json"
    if mf_path.exists():
        schema = load_embedded_schema("views-module-files.schema.json")
        data = _read_json(mf_path)
        for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
            findings.append(
                Finding(
                    rule_id="R-VIEW-004",
                    severity="warn",
                    target=str(mf_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"module_files.json schema 校验失败：{err.message}",
                )
            )

    # module_symbols schema
    ms_path = root / "views/module_symbols.json"
    if ms_path.exists():
        schema = load_embedded_schema("views-module-symbols.schema.json")
        data = _read_json(ms_path)
        for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
            findings.append(
                Finding(
                    rule_id="R-VIEW-005",
                    severity="warn",
                    target=str(ms_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"module_symbols.json schema 校验失败：{err.message}",
                )
            )

    # java_http_routes schema
    jr_path = root / "views/java_http_routes.json"
    if jr_path.exists():
        schema = load_embedded_schema("views-java-http-routes.schema.json")
        data = _read_json(jr_path)
        for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
            findings.append(
                Finding(
                    rule_id="R-VIEW-006",
                    severity="warn",
                    target=str(jr_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"java_http_routes.json schema 校验失败：{err.message}",
                )
            )

    # entry_graph schema（入口遍历图）
    eg_path = root / "views/entry_graph.json"
    if eg_path.exists():
        schema = load_embedded_schema("views-entry-graph.schema.json")
        data = _read_json(eg_path)
        for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
            findings.append(
                Finding(
                    rule_id="R-VIEW-007",
                    severity="warn",
                    target=str(eg_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"entry_graph.json schema 校验失败：{err.message}",
                )
            )

    # symbol_index jsonl schema（逐行校验，采样前 N 行避免巨型仓库卡死）
    si_path = root / "views/symbol_index.jsonl"
    if si_path.exists():
        schema = load_embedded_schema("views-symbol-index.schema.json")
        validator = Draft202012Validator(schema, registry=reg)
        try:
            max_lines = 2000
            with si_path.open("r", encoding="utf-8") as f:
                for i, line in enumerate(f):
                    if i >= max_lines:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except Exception as je:
                        findings.append(
                            Finding(
                                rule_id="R-VIEW-008",
                                severity="warn",
                                target=str(si_path.relative_to(cwd)),
                                path=f"/line/{i+1}",
                                message=f"symbol_index.jsonl 第 {i+1} 行不是合法 JSON：{je}",
                            )
                        )
                        continue
                    for err in validator.iter_errors(data):
                        findings.append(
                            Finding(
                                rule_id="R-VIEW-008",
                                severity="warn",
                                target=str(si_path.relative_to(cwd)),
                                path=f"/line/{i+1}/" + "/".join(str(p) for p in err.path),
                                message=f"symbol_index.jsonl schema 校验失败：{err.message}",
                            )
                        )
        except Exception as e:
            findings.append(
                Finding(
                    rule_id="R-VIEW-008",
                    severity="warn",
                    target=str(si_path.relative_to(cwd)),
                    path="/",
                    message=f"symbol_index.jsonl 读取/校验失败：{e}",
                )
            )

        # 覆盖率校验：roots 下的源码文件必须在 symbol_index 里出现（接近 100%）
        # 注意：对巨型仓库这一步可能较慢；可用 aise.yml strictSymbolCoverage 控制严重级别。
        exts = {".java", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}

        def _is_ignored(rel: str) -> bool:
            try:
                return any(match_glob(rel, pat) for pat in (cfg.ignore or []))
            except Exception:
                return False

        expected: set[str] = set()
        for r in (cfg.roots or ["src"]):
            rp = cwd / r
            if not rp.exists() or not rp.is_dir():
                continue
            for p in rp.rglob("*"):
                if not p.is_file():
                    continue
                if p.suffix.lower() not in exts:
                    continue
                rel = str(p.relative_to(cwd)).replace("\\", "/")
                if _is_ignored(rel):
                    continue
                expected.add(rel)

        # 兜底：把 module_files 中出现过的文件也纳入覆盖率集合（适配非 src 根的仓库）
        if mf_path.exists():
            try:
                mf = _read_json(mf_path)
                mods = mf.get("modules") if isinstance(mf, dict) else None
                if isinstance(mods, dict):
                    for m in mods.values():
                        if not isinstance(m, dict):
                            continue
                        for f2 in (m.get("files") or []):
                            if isinstance(f2, str) and f2 and not _is_ignored(f2):
                                if Path(f2).suffix.lower() in exts:
                                    expected.add(f2)
                for f2 in (mf.get("unmapped_files") or []):
                    if isinstance(f2, str) and f2 and not _is_ignored(f2):
                        if Path(f2).suffix.lower() in exts:
                            expected.add(f2)
            except Exception:
                pass

        covered: set[str] = set()
        try:
            with si_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(obj, dict) and isinstance(obj.get("file"), str):
                        covered.add(obj["file"])
        except Exception:
            pass

        if expected:
            missing = sorted(expected - covered)
            coverage = 1.0 - (len(missing) / max(1, len(expected)))
            findings.append(
                Finding(
                    rule_id="R-SYM-000",
                    severity="info",
                    target=str(si_path.relative_to(cwd)),
                    path="/",
                    message=f"symbol_index 覆盖率：{coverage*100:.2f}%（covered={len(expected)-len(missing)}/{len(expected)}）",
                )
            )
            if missing:
                sev = "error" if cfg.strict_symbol_coverage else "warn"
                sample = ", ".join(missing[:20])
                findings.append(
                    Finding(
                        rule_id="R-SYM-001",
                        severity=sev,
                        target=str(si_path.relative_to(cwd)),
                        path="/",
                        message=f"symbol_index 未覆盖 roots 下 {len(missing)} 个源码文件（示例：{sample}）",
                        suggestion="检查 roots/ignore 配置；或排查 tree-sitter 解析失败导致未写入记录。",
                    )
                )

    return findings


def validate_l1_static(cwd: Path) -> list[Finding]:
    """
    L1 静态校验（MVP）：先覆盖最关键的“可信性”问题：
    - index/schema
    - module/schema
    - api/flow/playbook schema + 引用存在性
    - 引用存在性（depends_on）
    - boundary.machine forbid_dependency 强校验
    - boundaries 至少包含 must/must_not
    """
    root = cwd / CODEWIKI_DIR
    cfg = load_config(cwd)
    findings: list[Finding] = []

    idx_path = root / "L1/index.json"
    if not idx_path.exists():
        findings.append(
            Finding(
                rule_id="R-IDX-001",
                severity="error",
                target=str(idx_path.relative_to(cwd)),
                path="/",
                message="缺少 L1/index.json（请先运行 aise init 或 aise scan）",
            )
        )
        return findings

    # registry：用于解析 common.schema.json 的 $ref（避免网络检索）
    common = load_embedded_schema("common.schema.json")
    reg = Registry().with_resource(
        "common.schema.json", Resource.from_contents(common, default_specification=DRAFT202012)
    )
    # 兼容：若 schema 内有 $id，也注册一份
    if isinstance(common, dict) and isinstance(common.get("$id"), str) and common["$id"]:
        reg = reg.with_resource(common["$id"], Resource.from_contents(common, default_specification=DRAFT202012))

    # index schema
    idx_schema = load_embedded_schema("index.schema.json")
    idx = _read_json(idx_path)
    for err in Draft202012Validator(idx_schema, registry=reg).iter_errors(idx):
        findings.append(
            Finding(
                rule_id="R-SCH-001",
                severity="error",
                target=str(idx_path.relative_to(cwd)),
                path="/" + "/".join(str(p) for p in err.path),
                message=f"index.json schema 校验失败：{err.message}",
            )
        )

    module_ids = set(idx.get("modules", []) or []) if isinstance(idx, dict) else set()
    api_ids = set(idx.get("apis", []) or []) if isinstance(idx, dict) else set()
    flow_ids = set(idx.get("flows", []) or []) if isinstance(idx, dict) else set()
    playbook_ids = set(idx.get("playbooks", []) or []) if isinstance(idx, dict) else set()
    relation_ids = set(idx.get("relations", []) or []) if isinstance(idx, dict) else set()

    # module schema + existence
    mod_schema = load_embedded_schema("module.schema.json")
    seen: set[str] = set()
    for mid in sorted(module_ids):
        if mid in seen:
            findings.append(
                Finding(
                    rule_id="R-NAME-001",
                    severity="error",
                    target=str(idx_path.relative_to(cwd)),
                    path="/modules",
                    message=f"moduleId 重复：{mid}",
                )
            )
            continue
        seen.add(mid)

        mod_path = _module_path(root, mid)
        if not mod_path.exists():
            findings.append(
                Finding(
                    rule_id="R-IDX-002",
                    severity="error",
                    target=str(idx_path.relative_to(cwd)),
                    path="/modules",
                    message=f"index 引用的 module 不存在：{mid}（期望文件 {mod_path.relative_to(cwd)}）",
                    suggestion="运行 aise scan 生成缺失模块，或从 index.modules 移除该条目。",
                )
            )
            continue

        mod = _read_json(mod_path)
        for err in Draft202012Validator(mod_schema, registry=reg).iter_errors(mod):
            findings.append(
                Finding(
                    rule_id="R-SCH-001",
                    severity="error",
                    target=str(mod_path.relative_to(cwd)),
                    path="/" + "/".join(str(p) for p in err.path),
                    message=f"module schema 校验失败：{err.message}",
                )
            )

        # R-BND-001：至少包含 must/must_not
        boundaries = (mod.get("boundaries", []) or []) if isinstance(mod, dict) else []
        if not any(b.get("type") in ("must", "must_not") for b in boundaries if isinstance(b, dict)):
            findings.append(
                Finding(
                    rule_id="R-BND-001",
                    severity="error",
                    target=str(mod_path.relative_to(cwd)),
                    path="/boundaries",
                    message="boundaries 至少需要一条 must 或 must_not（否则 L1 无法提供硬约束）",
                )
            )

        # 依赖存在性
        deps = ((mod.get("dependencies", {}) or {}).get("depends_on", []) or []) if isinstance(mod, dict) else []
        for dep in deps:
            if dep not in module_ids:
                findings.append(
                    Finding(
                        rule_id="R-REF-002",
                        severity="error",
                        target=str(mod_path.relative_to(cwd)),
                        path="/dependencies/depends_on",
                        message=f"依赖的 module 不存在：{dep}",
                    )
                )

        # forbid_dependency 强校验
        forbid_targets: set[str] = set()
        for b in boundaries:
            if not isinstance(b, dict):
                continue
            machine = b.get("machine")
            if isinstance(machine, dict) and machine.get("kind") == "forbid_dependency":
                for t in machine.get("targets", []) or []:
                    forbid_targets.add(str(t))
        for t in sorted(forbid_targets):
            if t in deps:
                findings.append(
                    Finding(
                        rule_id="R-BND-002",
                        severity="error",
                        target=str(mod_path.relative_to(cwd)),
                        path="/dependencies/depends_on",
                        message=f"违反 forbid_dependency：依赖中包含被禁止模块 {t}",
                    )
                )

        # Phase 5-B：layer 依赖矩阵校验（可配置）
        if isinstance(mod, dict):
            from_layer = str(mod.get("layer") or "unknown")
            allowed = cfg.layer_dependency_matrix.get(from_layer)
            if isinstance(allowed, list):
                for dep in deps:
                    dep_path = _module_path(root, dep)
                    dep_mod = _read_json(dep_path) if dep_path.exists() else {}
                    to_layer = str(dep_mod.get("layer") or "unknown") if isinstance(dep_mod, dict) else "unknown"
                    if to_layer != "unknown" and from_layer != "unknown" and to_layer not in allowed:
                        findings.append(
                            Finding(
                                rule_id="R-LAYER-001",
                                severity="error" if cfg.strict_layer_gate else "warn",
                                target=str(mod_path.relative_to(cwd)),
                                path="/dependencies/depends_on",
                                message=f"违反层级依赖矩阵：{mid}({from_layer}) -> {dep}({to_layer})",
                                suggestion="调整模块层级标注（layer）或通过 boundaries.machine 显式声明例外；否则应重构依赖方向。",
                            )
                        )

    # api/flow/playbook/relations：schema + existence
    api_schema = load_embedded_schema("api.schema.json")
    flow_schema = load_embedded_schema("flow.schema.json")
    pb_schema = load_embedded_schema("playbook.schema.json")
    rel_schema = load_embedded_schema("relations.schema.json")

    def _validate_collection(kind: str, ids: set[str], schema: dict[str, Any], base_dir: Path) -> None:
        seen2: set[str] = set()
        for _id in sorted(ids):
            if _id in seen2:
                findings.append(
                    Finding(
                        rule_id="R-NAME-002",
                        severity="error",
                        target=str(idx_path.relative_to(cwd)),
                        path=f"/{kind}",
                        message=f"{kind} id 重复：{_id}",
                    )
                )
                continue
            seen2.add(_id)

            p = base_dir / Path(_id + ".json")
            if not p.exists():
                findings.append(
                    Finding(
                        rule_id="R-IDX-003",
                        severity="error",
                        target=str(idx_path.relative_to(cwd)),
                        path=f"/{kind}",
                        message=f"index 引用的 {kind} 不存在：{_id}（期望文件 {p.relative_to(cwd)}）",
                    )
                )
                continue
            data = _read_json(p)
            for err in Draft202012Validator(schema, registry=reg).iter_errors(data):
                findings.append(
                    Finding(
                        rule_id="R-SCH-001",
                        severity="error",
                        target=str(p.relative_to(cwd)),
                        path="/" + "/".join(str(x) for x in err.path),
                        message=f"{kind} schema 校验失败：{err.message}",
                    )
                )

    _validate_collection("apis", api_ids, api_schema, root / "L1/apis")
    _validate_collection("flows", flow_ids, flow_schema, root / "L1/flows")
    _validate_collection("playbooks", playbook_ids, pb_schema, root / "L1/playbooks")
    _validate_collection("relations", relation_ids, rel_schema, root / "L1/relations")

    return findings


def validate_l1_diff(cwd: Path, base: str, head: str) -> list[Finding]:
    """
    diff 校验（MVP）：写入路径闸门
    - 从 git diff 获取 changed files
    - 用 views/filetree 映射到模块集合
    - 从模块 boundaries.machine 提取 allow_write_path / forbid_write_path
    - 对每个变更文件执行：
      - 命中 forbid_write_path -> error
      - 若存在 allow_write_path 且该文件不命中任何 allow -> warn（可在 strict 时升级为 error）
    """
    ensure_git(cwd)
    root = cwd / CODEWIKI_DIR
    cfg = load_config(cwd)
    findings: list[Finding] = []

    filetree_path = root / "views/filetree.json"
    if not filetree_path.exists():
        return [
            Finding(
                rule_id="R-VIEW-001",
                severity="error",
                target=str(filetree_path.relative_to(cwd)),
                path="/",
                message="缺少 views/filetree.json（无法做 diff 校验）；请先运行 aise scan。",
            )
        ]

    idx_path = root / "L1/index.json"
    if not idx_path.exists():
        return [
            Finding(
                rule_id="R-IDX-001",
                severity="error",
                target=str(idx_path.relative_to(cwd)),
                path="/",
                message="缺少 L1/index.json（无法做 diff 校验）；请先运行 aise scan。",
            )
        ]

    files = changed_files(cwd, base, head)
    # ignore（glob）过滤：避免把 build/target 等噪声纳入 gate
    if cfg.ignore:
        filtered: list[str] = []
        for f in files:
            if any(fnmatch.fnmatch(f, pat) for pat in cfg.ignore):
                continue
            filtered.append(f)
        files = filtered
    if not files:
        return findings

    filetree = _read_json(filetree_path)
    rules = _iter_filetree_rules(filetree)

    idx = _read_json(idx_path)
    module_ids = set(idx.get("modules", []) or []) if isinstance(idx, dict) else set()

    # 读取每个模块的 gate 规则（allow/forbid write）
    allow_by_module: dict[str, list[str]] = {}
    forbid_by_module: dict[str, list[str]] = {}
    for mid in module_ids:
        mp = _module_path(root, mid)
        if not mp.exists():
            continue
        mod = _read_json(mp)
        boundaries = (mod.get("boundaries", []) or []) if isinstance(mod, dict) else []
        allow: list[str] = []
        forbid: list[str] = []
        for b in boundaries:
            if not isinstance(b, dict):
                continue
            machine = b.get("machine")
            if not isinstance(machine, dict):
                continue
            kind = machine.get("kind")
            targets = [str(x) for x in (machine.get("targets", []) or [])]
            if kind == "allow_write_path":
                allow.extend(targets)
            elif kind == "forbid_write_path":
                forbid.extend(targets)
        if allow:
            allow_by_module[mid] = allow
        if forbid:
            forbid_by_module[mid] = forbid

    def modules_for_file(f: str) -> set[str]:
        out: set[str] = set()
        for _prio, rule, mapping in rules:
            if rule.matches(f):
                for mid in mapping["targets"].get("modules", []) or []:
                    out.add(mid)
        return out

    def match_any(path: str, patterns: list[str]) -> bool:
        for pat in patterns:
            if fnmatch.fnmatch(path, pat):
                return True
        return False

    for f in files:
        mods = modules_for_file(f)
        if not mods:
            findings.append(
                Finding(
                    rule_id="R-DIFF-003",
                    severity="warn",
                    target="git",
                    path=f,
                    message=f"变更文件未能映射到任何模块（filetree 规则可能过粗/缺失）：{f}",
                    suggestion="补充 views/filetree.json mapping（更具体 glob + priority）。",
                )
            )
            continue

        # forbid 优先：任意模块 forbid 命中即 error
        forbids: list[str] = []
        allows: list[str] = []
        for mid in mods:
            forbids.extend(forbid_by_module.get(mid, []))
            allows.extend(allow_by_module.get(mid, []))

        if forbids and match_any(f, forbids):
            findings.append(
                Finding(
                    rule_id="R-DIFF-001",
                    severity="error",
                    target="git",
                    path=f,
                    message=f"写入路径闸门触发：文件命中 forbid_write_path（modules={sorted(mods)}）",
                )
            )
            continue

        if allows and not match_any(f, allows):
            findings.append(
                Finding(
                    rule_id="R-DIFF-002",
                    severity="error" if cfg.strict_diff_gate else "warn",
                    target="git",
                    path=f,
                    message=(
                        "写入路径闸门提示：相关模块存在 allow_write_path 白名单，但该文件未命中任何 allow 模式。"
                        f" modules={sorted(mods)}"
                    ),
                    suggestion="若这是合法修改：更新对应模块 boundaries.machine.allow_write_path；否则避免修改该路径。",
                )
            )

    return findings


def validation_report(findings: list[Finding]) -> dict[str, Any]:
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warn")
    infos = sum(1 for f in findings if f.severity == "info")
    return {
        "version": "0.1",
        "summary": {"errors": errors, "warnings": warnings, "infos": infos},
        "findings": [f.to_dict() for f in findings],
    }


def current_commit_for_provenance(cwd: Path) -> str:
    return head_commit(cwd)
