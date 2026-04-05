from __future__ import annotations

import re
from pathlib import Path

from .base import CMakeTarget, Entrypoint, ExtractResult


_re_cmd = re.compile(r"(?is)^\s*([a-z_0-9]+)\s*\((.*?)\)\s*$")


def _strip_comments(text: str) -> str:
    # 简化：去掉行内 # 注释（不处理字符串内 #）
    out = []
    for line in text.splitlines():
        if "#" in line:
            line = line.split("#", 1)[0]
        out.append(line)
    return "\n".join(out)


def _tokenize_args(arg_blob: str) -> list[str]:
    # 极简分词：按空白拆，不处理引号/变量展开
    return [a for a in re.split(r"\s+", arg_blob.strip()) if a]


def parse_cmakelists(text: str) -> dict[str, CMakeTarget]:
    """
    CMakeLists.txt 极简解析（v1）：
    - add_library(name ...)
    - add_executable(name ...)
    - target_link_libraries(name ...)
    - 不解析变量/生成器表达式，先求“有 > 无”
    """
    text = _strip_comments(text)
    targets: dict[str, CMakeTarget] = {}

    # 将多行命令拼成一行（粗糙：直到括号平衡）
    buf = []
    depth = 0
    stmts: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch == "\n" and depth > 0:
            buf.append(" ")
        else:
            buf.append(ch)
    flattened = "".join(buf)
    for line in flattened.splitlines():
        s = line.strip()
        if not s:
            continue
        stmts.append(s)

    for s in stmts:
        m = _re_cmd.match(s)
        if not m:
            continue
        cmd = m.group(1).lower()
        args = _tokenize_args(m.group(2))
        if not args:
            continue

        if cmd == "add_library":
            name = args[0]
            sources = [a for a in args[1:] if a.endswith((".c", ".cc", ".cpp", ".cxx"))]
            targets[name] = CMakeTarget(name=name, kind="library", sources=sources, links=[])
        elif cmd == "add_executable":
            name = args[0]
            sources = [a for a in args[1:] if a.endswith((".c", ".cc", ".cpp", ".cxx"))]
            targets[name] = CMakeTarget(name=name, kind="executable", sources=sources, links=[])
        elif cmd == "target_link_libraries":
            name = args[0]
            if name not in targets:
                targets[name] = CMakeTarget(name=name, kind="unknown", sources=[], links=[])
            # 丢弃 PUBLIC/PRIVATE/INTERFACE
            links = [a for a in args[1:] if a.upper() not in ("PUBLIC", "PRIVATE", "INTERFACE")]
            targets[name] = CMakeTarget(
                name=targets[name].name,
                kind=targets[name].kind,
                sources=targets[name].sources,
                links=sorted(set(targets[name].links + links)),
            )

    return targets


def parse_cmakelists_with_subdirs(text: str) -> tuple[dict[str, CMakeTarget], list[str]]:
    """
    在 parse_cmakelists 的基础上，额外抽取 add_subdirectory(dir) 列表（v1）。
    - 仅抽取字面量路径（跳过包含 ${} 的表达式）
    """
    text = _strip_comments(text)
    targets = parse_cmakelists(text)

    subdirs: list[str] = []
    # 将多行命令拼成一行（复用与 parse_cmakelists 一致的策略）
    buf = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(depth - 1, 0)
        if ch == "\n" and depth > 0:
            buf.append(" ")
        else:
            buf.append(ch)
    flattened = "".join(buf)
    for line in flattened.splitlines():
        s = line.strip()
        if not s:
            continue
        m = _re_cmd.match(s)
        if not m:
            continue
        cmd = m.group(1).lower()
        if cmd != "add_subdirectory":
            continue
        args = _tokenize_args(m.group(2))
        if not args:
            continue
        d = args[0]
        if "${" in d:
            continue
        subdirs.append(d.strip().strip('"').strip("'"))
    return targets, sorted(set(subdirs))


def extract(repo_root: Path) -> ExtractResult:
    """
    从 CMakeLists.txt 抽：
    - extra_modules：build/cmake/<target>（可选）
    - entrypoints：executable targets 作为 entrypoints（other）
    """
    cmake = repo_root / "CMakeLists.txt"
    if not cmake.exists():
        return ExtractResult()

    # 递归解析：root + add_subdirectory 的 CMakeLists（深度/数量限制避免跑飞）
    visited: set[Path] = set()
    queue: list[Path] = [cmake]
    all_targets: dict[str, CMakeTarget] = {}

    max_files = 30
    while queue and len(visited) < max_files:
        p = queue.pop(0)
        if p in visited or not p.exists():
            continue
        visited.add(p)
        text = p.read_text(encoding="utf-8", errors="replace")
        targets, subdirs = parse_cmakelists_with_subdirs(text)
        for k, v in targets.items():
            # 同名 target：后者覆盖前者（简化）
            all_targets[k] = v
        for d in subdirs:
            child = (p.parent / d / "CMakeLists.txt").resolve()
            # 只允许 repo 内
            try:
                child.relative_to(repo_root.resolve())
            except Exception:
                continue
            queue.append(child)

    extra_modules: list[str] = []
    entrypoints: list[Entrypoint] = []
    module_depends_on: dict[str, list[str]] = {}

    # 预计算 target -> module_id
    target_mid: dict[str, str] = {}
    for tname in all_targets.keys():
        target_mid[tname] = f"build/cmake/{tname}".lower().replace("_", "-")

    for t in all_targets.values():
        mid = f"build/cmake/{t.name}".lower().replace("_", "-")
        extra_modules.append(mid)
        # 依赖：只保留“指向另一个已知 target 的 link”，外部库忽略
        deps = [target_mid[x] for x in t.links if x in target_mid and target_mid[x] != mid]
        if deps:
            module_depends_on[mid] = sorted(set(deps))
        if t.kind == "executable":
            entrypoints.append(
                Entrypoint(
                    id=f"cmake.exe.{t.name}",
                    type="other",
                    match_kind="literal",
                    match_value=t.name,
                    summary=f"CMake executable target: {t.name}",
                    modules=[mid, "build/cmake"],
                    source_kind="file",
                    source_ref="CMakeLists.txt",
                )
            )

    # googletest 等仓库顶层可能没有 add_executable；但我们仍希望有一个“构建入口点”。
    if not entrypoints:
        entrypoints.append(
            Entrypoint(
                id="cmake.build",
                type="other",
                match_kind="literal",
                match_value="cmake",
                summary="CMake build entrypoint (configure/build/test via CMake/CTest)",
                modules=["build/cmake"],
                source_kind="file",
                source_ref="CMakeLists.txt",
            )
        )

    return ExtractResult(
        entrypoints=entrypoints,
        extra_modules=sorted(set(extra_modules)),
        module_depends_on=module_depends_on,
    )
