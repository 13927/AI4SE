from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .llm_openai import OpenAIClient, load_openai_config


PLACEHOLDER_RE = re.compile(r"（待补充|（自动生成|TODO|TBD|placeholder", re.IGNORECASE)


def is_placeholder_text(s: str) -> bool:
    return bool(PLACEHOLDER_RE.search(s or ""))


def count_placeholders(text: str) -> int:
    return len(PLACEHOLDER_RE.findall(text or ""))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def guess_module_responsibility(module_id: str) -> str:
    mid = module_id.lower()
    if mid.startswith("build/cmake"):
        return "CMake 构建系统相关：targets/依赖/构建脚本与测试执行（ctest）。"
    if mid.startswith("build/maven"):
        return "Maven 构建与依赖管理（pom.xml、多模块、测试执行 mvn test）。"
    if mid.startswith("ext/maven/"):
        return f"外部 Maven 依赖模块：{module_id}（由 scan 自动归档，用于依赖图与影响分析）。"
    if mid in ("app/java", "app/resources"):
        return "Java 应用代码/资源：业务逻辑、Controller/Service/Repository 及资源文件。"
    if mid.startswith("test/java"):
        return "Java 测试代码：单元测试/集成测试与测试夹具。"
    if mid.startswith("cpp/include"):
        return "C/C++ 公共头文件：对外接口面（include/ 导出）。"
    if mid.startswith("cpp/src"):
        return "C/C++ 实现源码：库/可执行文件的实现与内部逻辑。"
    if mid.startswith("cpp/googletest"):
        return "googletest 框架源码：测试框架核心实现与构建组织。"
    if mid.startswith("cpp/googlemock"):
        return "googlemock 框架源码：mock 能力实现与构建组织。"
    if mid.startswith("biz/"):
        return f"业务模块：{module_id}（由入口/包结构推断，需按业务边界进一步校准）。"
    if mid.startswith("runtime/"):
        return f"运行时模块：{module_id}，负责 agent/runtime 相关能力。"
    if mid == "core":
        return "全局兜底模块：当未能精确归类时用于承载默认规则与全局约束。"
    return f"模块 {module_id}：职责需进一步细化（已由工具生成最小可执行描述）。"


def guess_module_invariants(module_id: str) -> list[str]:
    mid = module_id.lower()
    if mid.startswith("build/"):
        return [
            "任何构建脚本修改必须确保本地/CI 构建与测试可通过。",
            "避免引入不可复现的构建步骤；依赖变更需记录原因与影响范围。",
        ]
    if mid.startswith("test/") or mid.endswith("/tests") or mid.startswith("cpp/tests"):
        return [
            "测试用例应可重复执行且彼此隔离；失败需提供可定位日志。",
            "新增功能必须补齐对应测试，并保证 CI 通过。",
        ]
    if mid.startswith("ext/"):
        return [
            "外部依赖条目仅用于依赖图与影响分析，不应承载业务代码。",
        ]
    return [
        "保持模块边界清晰；跨模块修改需显式说明与评审。",
        "对外接口变更需同步更新相关文档与测试。",
    ]


def _fill_l1_module(mod: dict[str, Any]) -> bool:
    changed = False
    mid = str(mod.get("id") or "")

    resp = str(mod.get("responsibility") or "")
    if not resp or is_placeholder_text(resp):
        mod["responsibility"] = guess_module_responsibility(mid)
        changed = True

    inv = mod.get("invariants")
    if isinstance(inv, list) and any(isinstance(x, str) and is_placeholder_text(x) for x in inv):
        mod["invariants"] = guess_module_invariants(mid)
        changed = True

    # public_interfaces summary
    pis = mod.get("public_interfaces")
    if isinstance(pis, list):
        for it in pis:
            if not isinstance(it, dict):
                continue
            summ = str(it.get("summary") or "")
            if summ and is_placeholder_text(summ):
                it["summary"] = f"{mid} 对外接口（需按项目实际完善）。"
                changed = True
    return changed


def _fill_l1_flow(flow: dict[str, Any]) -> bool:
    changed = False
    fid = str(flow.get("id") or "")
    name = str(flow.get("name") or fid)
    summ = str(flow.get("summary") or "")
    if not summ or is_placeholder_text(summ):
        flow["summary"] = f"{name}：由 aise 基于 entrypoints/apis 推导的结构链路（可继续细化）。"
        changed = True
    if not flow.get("name") or is_placeholder_text(str(flow.get("name") or "")):
        flow["name"] = name if name else fid
        changed = True
    return changed


def _fill_l1_playbook(pb: dict[str, Any]) -> bool:
    changed = False
    pid = str(pb.get("id") or "")
    name = str(pb.get("name") or pid)
    summ = str(pb.get("summary") or "")
    if not summ or is_placeholder_text(summ):
        pb["summary"] = f"{name}：可执行变更手册（步骤与验证命令可按项目实际补强）。"
        changed = True
    if not pb.get("name") or is_placeholder_text(str(pb.get("name") or "")):
        pb["name"] = name if name else pid
        changed = True
    return changed


def _section_replace(md: str, heading: str, new_lines: list[str]) -> str:
    """
    替换某个二级标题下的内容（直到下一个 ## 或 EOF）。
    若找不到 heading，则原样返回。
    """
    pat = re.compile(rf"(^##\s+{re.escape(heading)}\s*$)", re.MULTILINE)
    m = pat.search(md)
    if not m:
        return md
    start = m.end()
    # find next ##
    n = re.search(r"(?m)^##\s+", md[start:])
    end = start + n.start() if n else len(md)
    before = md[: start] + "\n\n"
    after = md[end:]
    body = "\n".join(new_lines).rstrip() + "\n\n"
    return before + body + after.lstrip("\n")


def fill_l2_module(md: str, mod: dict[str, Any]) -> tuple[str, bool]:
    """
    仅替换包含（待补充）的段落；若文件几乎全是占位，则重写关键段落。
    """
    if "（待补充" not in md and "（自动生成" not in md:
        return md, False

    mid = str(mod.get("id") or "")
    deps = ((mod.get("dependencies") or {}).get("depends_on") or []) if isinstance(mod.get("dependencies"), dict) else []
    apis = (mod.get("entrypoints") or []) if isinstance(mod.get("entrypoints"), list) else []

    changed = False
    # 设计意图
    md2 = _section_replace(
        md,
        "设计意图",
        [
            f"- {mod.get('responsibility')}",
            "- 该段由 aise 自动补全（可在此基础上继续细化具体设计选择）。",
        ],
    )
    changed = changed or (md2 != md)
    md = md2

    # 隐含假设
    md2 = _section_replace(
        md,
        "隐含假设",
        [
            "- 模块边界与依赖关系以 docs/codewiki/L1 为准。",
            "- 修改将遵守 writeScopes 与 diff gate；必要时通过 request_upgrade 扩权。",
        ],
    )
    changed = changed or (md2 != md)
    md = md2

    # 修改指南
    verify_hint = "mvn test" if "java" in mid else ("ctest" if "cpp" in mid or "cmake" in mid else "pytest")
    md2 = _section_replace(
        md,
        "修改指南",
        [
            "- 修改前：先确认影响的入口点与依赖模块（L1.dependencies.depends_on）。",
            f"- 依赖模块：{', '.join(deps) if deps else '（无）'}",
            f"- 相关入口点：{', '.join(apis) if apis else '（无）'}",
            f"- 修改后：运行验证命令（建议）：`{verify_hint}`，并更新对应 L1/L2 条目。",
        ],
    )
    changed = changed or (md2 != md)
    md = md2
    return md, changed


def fill_l2_api(md: str, api: dict[str, Any]) -> tuple[str, bool]:
    if "（待补充" not in md and "（自动生成" not in md:
        return md, False

    aid = str(api.get("id") or "")
    proto = str(api.get("protocol") or "other")
    summary = str(api.get("summary") or "")
    changed = False

    if proto == "http":
        http = api.get("http") or {}
        method = str((http.get("method") or "GET")).upper()
        path = str(http.get("path") or "/")
        handler = http.get("handler") or {}
        file = str(handler.get("file") or "")
        symbol = str(handler.get("symbol") or "")
        purpose = [f"- 该 API 提供 HTTP 路由：`{method} {path}`。", f"- 处理入口：`{symbol}`（{file}）。", f"- 摘要：{summary}"]
        example = [f"```bash\ncurl -X {method} http://localhost:8080{path}\n```"]
    elif proto == "cpp-header":
        h = api.get("cpp_header") or {}
        inc = str(h.get("include_path") or "")
        file = str(h.get("file") or "")
        purpose = [f"- 该 API 表示公共头文件接口面：`#include <{inc}>`。", f"- 文件：{file}", f"- 摘要：{summary}"]
        example = [f"```cpp\n#include <{inc}>\n// ...\n```"]
    else:
        purpose = [f"- 摘要：{summary or aid}"]
        example = ["- （示例待按实际补充）"]

    md2 = _section_replace(md, "用途", purpose)
    changed = changed or (md2 != md)
    md = md2

    md2 = _section_replace(
        md,
        "约束",
        [
            "- 修改该 API 时必须同步更新对应的 CodeWiki 条目（L1/L2）并通过 aise validate。",
            "- 若涉及权限/鉴权/兼容性要求，请在此明确。",
        ],
    )
    changed = changed or (md2 != md)
    md = md2

    md2 = _section_replace(md, "示例", example)
    changed = changed or (md2 != md)
    md = md2
    return md, changed


def fill_l2_flow(md: str, flow: dict[str, Any]) -> tuple[str, bool]:
    if "（待补充" not in md and "（自动生成" not in md:
        return md, False
    fid = str(flow.get("id") or "")
    summary = str(flow.get("summary") or fid)
    if is_placeholder_text(summary):
        summary = str(flow.get("name") or fid) or fid
    stages = flow.get("stages") or []
    stage_lines: list[str] = [f"- 摘要：{summary}", "- stages："]
    for st in stages:
        if not isinstance(st, dict):
            continue
        sid = st.get("id")
        ssum = st.get("summary")
        mods = ", ".join(st.get("modules") or [])
        apis = ", ".join(st.get("apis") or [])
        stage_lines.append(f"  - {sid}: {ssum} (modules=[{mods}] apis=[{apis}])")
    md2 = _section_replace(md, "说明", stage_lines)
    return md2, md2 != md


def fill_l2_playbook(md: str, pb: dict[str, Any]) -> tuple[str, bool]:
    if "（待补充" not in md and "（自动生成" not in md:
        return md, False
    pid = str(pb.get("id") or "")
    summary = str(pb.get("summary") or pid)
    if is_placeholder_text(summary):
        summary = str(pb.get("name") or pid) or pid
    steps = pb.get("steps") or []
    vers = pb.get("verifications") or []
    lines = [f"- 摘要：{summary}", "- 步骤："]
    for s in steps:
        lines.append(f"  - {s}")
    if vers:
        lines.append("- 验证：")
        for v in vers:
            lines.append(f"  - `{v}`")
    md2 = _section_replace(md, "说明", lines)
    return md2, md2 != md


@dataclass
class FillOptions:
    use_llm: bool = False


def maybe_use_llm_default() -> bool:
    try:
        load_openai_config()
        return True
    except Exception:
        return False


def _llm_json(client: OpenAIClient, *, system: str, user: str) -> dict[str, Any]:
    """
    让 LLM 输出 JSON（用于可审计、可控的文档填充）。
    """
    resp = client.chat_completions(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    content = (
        ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
        if isinstance(resp, dict)
        else ""
    )
    if not isinstance(content, str):
        raise RuntimeError("LLM 返回格式异常：缺少 message.content")
    # 宽松提取 JSON（容忍前后有解释，但优先找第一个 {...}）
    m = re.search(r"\{[\s\S]*\}", content)
    if not m:
        raise RuntimeError(f"LLM 未返回 JSON：{content[:200]}")
    return json.loads(m.group(0))


def _llm_markdown(client: OpenAIClient, *, system: str, user: str) -> str:
    """
    让 LLM 输出 Markdown（用于面向人阅读的概览文档）。
    """
    resp = client.chat_completions(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    content = (
        ((resp.get("choices") or [{}])[0].get("message") or {}).get("content")
        if isinstance(resp, dict)
        else ""
    )
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("LLM 返回为空（Markdown）")
    return content.strip() + "\n"


def _build_human_overview_context(repo_root: Path) -> dict[str, Any]:
    root = repo_root / "docs/codewiki"
    idx = _read_json(root / "L1/index.json") if (root / "L1/index.json").exists() else {}
    views_routes = _read_json(root / "views/java_http_routes.json") if (root / "views/java_http_routes.json").exists() else {}
    rel = _read_json(root / "L1/relations/relations/java/http-di.json") if (root / "L1/relations/relations/java/http-di.json").exists() else {}
    flow_java = _read_json(root / "L1/flows/flow/java/http-request.json") if (root / "L1/flows/flow/java/http-request.json").exists() else {}
    flow_cpp = _read_json(root / "L1/flows/flow/cpp/build-and-test.json") if (root / "L1/flows/flow/cpp/build-and-test.json").exists() else {}

    # routes sample
    routes_sample: list[dict[str, Any]] = []
    if isinstance(views_routes, dict):
        for r in (views_routes.get("routes") or [])[:12]:
            if not isinstance(r, dict):
                continue
            chain = r.get("chain") or []
            # 只取 role+class+module（便于人读）
            chain2 = []
            if isinstance(chain, list):
                for n in chain[:4]:
                    if isinstance(n, dict):
                        chain2.append({"role": n.get("role"), "class": n.get("class"), "module": n.get("module")})
            routes_sample.append(
                {
                    "method": r.get("method"),
                    "path": r.get("path"),
                    "handler_class": r.get("handler_class"),
                    "chain": chain2,
                }
            )

    # relations edges sample
    edges_sample: list[dict[str, Any]] = []
    if isinstance(rel, dict):
        for e in (rel.get("edges") or [])[:20]:
            if isinstance(e, dict):
                edges_sample.append(
                    {
                        "from": e.get("from_module"),
                        "to": e.get("to_module"),
                        "type": e.get("type"),
                        "weight": e.get("weight"),
                        "evidence": (e.get("evidence") or [])[:3],
                    }
                )

    return {
        "index": {
            "modules": (idx.get("modules") or [])[:80] if isinstance(idx, dict) else [],
            "apis_sample": (idx.get("apis") or [])[:20] if isinstance(idx, dict) else [],
            "flows": (idx.get("flows") or []) if isinstance(idx, dict) else [],
            "playbooks": (idx.get("playbooks") or []) if isinstance(idx, dict) else [],
            "relations": (idx.get("relations") or []) if isinstance(idx, dict) else [],
        },
        "java": {"routes_sample": routes_sample, "relations_edges_sample": edges_sample, "flow_http": flow_java},
        "cpp": {"flow_build_test": flow_cpp},
    }


def _llm_fill_human_overview(client: OpenAIClient, *, repo_root: Path) -> str:
    """
    生成 docs/codewiki/HUMAN_OVERVIEW.md（面向人阅读）。
    约束：
    - 必须可核对：引用到 CodeWiki 中的具体文件路径（相对 docs/codewiki）。
    - 必须包含至少一个 Mermaid 图（flowchart 或 sequenceDiagram）。
    - 禁止占位词（待补充/TODO/TBD/自动生成/placeholder）。
    """
    ctx = _build_human_overview_context(repo_root)
    system = (
        "你是软件工程文档作者。你要为一个仓库的 CodeWiki 生成“面向人阅读”的概览文档。"
        "要求：1) 用中文；2) 只基于给定 JSON 上下文，不要编造；3) 输出 Markdown；"
        "4) 必须包含 Mermaid 图；5) 不要出现占位词（待补充/TODO/TBD/自动生成/placeholder）。"
    )
    user = (
        "请生成文件 `docs/codewiki/HUMAN_OVERVIEW.md` 的完整内容，结构建议：\n"
        "1) 项目是做什么的（从 modules/apis/routes 推断，保持谨慎措辞）\n"
        "2) 开发者最常见的入口（HTTP 路由或公开头文件）\n"
        "3) 模块视图（列出关键模块 + 一张模块关系图）\n"
        "4) 关键链路（HTTP 请求链路或构建测试链路，给 Mermaid 图）\n"
        "5) 如何核对（告诉读者去哪些 L1/views 文件验证）\n\n"
        "上下文 JSON：\n"
        f"{json.dumps(ctx, ensure_ascii=False, indent=2)}\n"
    )
    md = _llm_markdown(client, system=system, user=user)
    if count_placeholders(md) > 0:
        raise RuntimeError("HUMAN_OVERVIEW.md 仍包含占位词，拒绝写入。")
    return md

def _llm_fill_module(
    client: OpenAIClient,
    *,
    mid: str,
    mod: dict[str, Any],
    module_files_stat: dict[str, Any] | None,
    module_symbols_stat: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    用 LLM 填充 module 的 responsibility/invariants + L2 三段内容（只用于替换占位）。
    """
    # 上下文（尽量短：只给统计与少量样例）
    files_count = 0
    top_ext = []
    if isinstance(module_files_stat, dict):
        files_count = int(module_files_stat.get("count", 0) or 0)
        cbe = module_files_stat.get("counts_by_ext") or {}
        if isinstance(cbe, dict):
            top_ext = sorted(cbe.items(), key=lambda x: int(x[1]), reverse=True)[:5]
    classes_sample = []
    if isinstance(module_symbols_stat, dict):
        cls = module_symbols_stat.get("classes") or []
        if isinstance(cls, list):
            for c in cls[:12]:
                if isinstance(c, dict):
                    classes_sample.append(
                        {
                            "name": c.get("name"),
                            "package": c.get("package"),
                            "stereotype": c.get("stereotype"),
                            "file": c.get("file"),
                        }
                    )

    system = (
        "你是一个软件工程助手，任务是为仓库的 CodeWiki 模块补全可审计文档。"
        "你必须：1) 用中文；2) 不要编造外部事实；3) 不要输出占位词（如：待补充/TODO/TBD/自动生成）；"
        "4) 输出必须是严格 JSON；5) 内容要短、工程化、可用于代码评审。"
    )
    user = json.dumps(
        {
            "task": "fill_module",
            "module": {
                "id": mid,
                "name": mod.get("name"),
                "module_kind": mod.get("module_kind"),
                "layer": mod.get("layer"),
                "depends_on": ((mod.get("dependencies") or {}).get("depends_on") or []),
                "files_count": files_count,
                "top_ext": top_ext,
                "classes_sample": classes_sample,
            },
            "output_schema": {
                "responsibility": "string（1-2 句）",
                "invariants": "string[]（2-6 条，具体可验证）",
                "l2": {
                    "design_intent": "string[]（要点列表）",
                    "assumptions": "string[]（要点列表）",
                    "change_guide": "string[]（要点列表，含建议跑的测试/回归项）",
                },
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    out = _llm_json(client, system=system, user=user)
    if not isinstance(out, dict):
        raise RuntimeError("LLM 输出非 object")
    return out


def fill_repo_codewiki(repo_root: Path, *, use_llm: bool = False) -> dict[str, Any]:
    """
    补齐占位（不会覆盖非占位内容）：
    - L1/modules 的 responsibility/invariants/public_interfaces.summary
    - L2/modules 的 设计意图/隐含假设/修改指南段落
    返回统计信息。
    """
    root = repo_root / "docs/codewiki"
    idx_path = root / "L1/index.json"
    if not idx_path.exists():
        raise RuntimeError("缺少 docs/codewiki/L1/index.json，请先运行 aise init + aise scan")

    client: OpenAIClient | None = None
    if use_llm:
        # 仅在显式 use_llm 或默认启用时初始化（避免无 key 时影响非 LLM 流程）
        client = OpenAIClient(load_openai_config())

    idx = _read_json(idx_path)
    modules = idx.get("modules") or []
    apis = idx.get("apis") or []
    flows = idx.get("flows") or []
    playbooks = idx.get("playbooks") or []

    mf = _read_json(root / "views/module_files.json") if (root / "views/module_files.json").exists() else {}
    ms = _read_json(root / "views/module_symbols.json") if (root / "views/module_symbols.json").exists() else {}
    mf_mods = mf.get("modules") if isinstance(mf, dict) else {}
    ms_mods = ms.get("modules") if isinstance(ms, dict) else {}

    changed_modules = 0
    changed_l2 = 0
    changed_l2_apis = 0
    changed_l2_flows = 0
    changed_l2_playbooks = 0
    human_overview_updated = 0

    for mid in modules:
        mp = root / "L1/modules" / Path(mid + ".json")
        if not mp.exists():
            continue
        mod = _read_json(mp)
        if isinstance(mod, dict):
            changed = _fill_l1_module(mod)
            # LLM：只对 code/biz 模块尝试把占位升级为更具体描述（避免对 ext/build 产生大量调用）
            if client is not None:
                mk = str(mod.get("module_kind") or "unknown")
                if mk == "code" or mid.startswith("biz/"):
                    # 仅在 responsibility/invariants 仍是占位时才请求 LLM（控制成本）
                    resp_txt = str(mod.get("responsibility") or "")
                    inv = mod.get("invariants") or []
                    need_llm = (not resp_txt) or is_placeholder_text(resp_txt) or any(
                        isinstance(x, str) and is_placeholder_text(x) for x in (inv if isinstance(inv, list) else [])
                    )
                    if need_llm:
                        out = _llm_fill_module(
                            client,
                            mid=mid,
                            mod=mod,
                            module_files_stat=(mf_mods.get(mid) if isinstance(mf_mods, dict) else None),
                            module_symbols_stat=(ms_mods.get(mid) if isinstance(ms_mods, dict) else None),
                        )
                        if isinstance(out.get("responsibility"), str) and not is_placeholder_text(out["responsibility"]):
                            mod["responsibility"] = out["responsibility"].strip()
                            changed = True
                        inv2 = out.get("invariants")
                        if isinstance(inv2, list) and inv2 and all(isinstance(x, str) for x in inv2):
                            inv2c = [x.strip() for x in inv2 if x.strip() and not is_placeholder_text(x)]
                            if inv2c:
                                mod["invariants"] = inv2c
                                changed = True

                        # L2：用 LLM 输出替换占位段落
                        l2p = root / "L2/modules" / Path(mid + ".md")
                        if l2p.exists():
                            md = _read_text(l2p)
                            l2 = out.get("l2") if isinstance(out, dict) else None
                            if isinstance(l2, dict):
                                di = l2.get("design_intent") if isinstance(l2.get("design_intent"), list) else []
                                asmp = l2.get("assumptions") if isinstance(l2.get("assumptions"), list) else []
                                cg = l2.get("change_guide") if isinstance(l2.get("change_guide"), list) else []
                                # 复用现有 section_replace 工具：把每段替换为要点
                                lines = []
                                for x in di[:10]:
                                    if isinstance(x, str) and x.strip() and not is_placeholder_text(x):
                                        lines.append(f"- {x.strip()}")
                                if lines:
                                    md2 = _section_replace(md, "设计意图", lines)
                                    md = md2
                                lines = []
                                for x in asmp[:10]:
                                    if isinstance(x, str) and x.strip() and not is_placeholder_text(x):
                                        lines.append(f"- {x.strip()}")
                                if lines:
                                    md2 = _section_replace(md, "隐含假设", lines)
                                    md = md2
                                lines = []
                                for x in cg[:12]:
                                    if isinstance(x, str) and x.strip() and not is_placeholder_text(x):
                                        lines.append(f"- {x.strip()}")
                                if lines:
                                    md2 = _section_replace(md, "修改指南", lines)
                                    md = md2
                                # 写回
                                _write_text(l2p, md)
                                changed_l2 += 1

            if changed:
                _write_json(mp, mod)
                changed_modules += 1

        # 启发式补全 L2（作为 LLM 的兜底，确保不残留占位）
        l2p = root / "L2/modules" / Path(mid + ".md")
        if l2p.exists() and isinstance(mod, dict):
            md = _read_text(l2p)
            md2, ch = fill_l2_module(md, mod)
            if ch:
                _write_text(l2p, md2)
                changed_l2 += 1

    # L1 flows/playbooks：先修正 summary/name 的占位，确保导出 0 占位
    for fid in flows:
        fp = root / "L1/flows" / Path(fid + ".json")
        if fp.exists():
            flow = _read_json(fp)
            if isinstance(flow, dict) and _fill_l1_flow(flow):
                _write_json(fp, flow)
    for pid in playbooks:
        pp = root / "L1/playbooks" / Path(pid + ".json")
        if pp.exists():
            pb = _read_json(pp)
            if isinstance(pb, dict) and _fill_l1_playbook(pb):
                _write_json(pp, pb)

    # L2/apis
    for aid in apis:
        ap = root / "L1/apis" / Path(aid + ".json")
        if not ap.exists():
            continue
        api = _read_json(ap)
        l2p = root / "L2/apis" / Path(aid + ".md")
        if l2p.exists() and isinstance(api, dict):
            md = _read_text(l2p)
            md2, ch = fill_l2_api(md, api)
            if ch:
                _write_text(l2p, md2)
                changed_l2_apis += 1

    # L2/flows
    for fid in flows:
        fp = root / "L1/flows" / Path(fid + ".json")
        if not fp.exists():
            continue
        flow = _read_json(fp)
        l2p = root / "L2/flows" / Path(fid + ".md")
        if l2p.exists() and isinstance(flow, dict):
            md = _read_text(l2p)
            md2, ch = fill_l2_flow(md, flow)
            if ch:
                _write_text(l2p, md2)
                changed_l2_flows += 1

    # L2/playbooks
    for pid in playbooks:
        pp = root / "L1/playbooks" / Path(pid + ".json")
        if not pp.exists():
            continue
        pb = _read_json(pp)
        l2p = root / "L2/playbooks" / Path(pid + ".md")
        if l2p.exists() and isinstance(pb, dict):
            md = _read_text(l2p)
            md2, ch = fill_l2_playbook(md, pb)
            if ch:
                _write_text(l2p, md2)
                changed_l2_playbooks += 1

    # 面向人阅读的概览（仅在 use_llm 时生成/覆盖）
    if client is not None:
        hop = root / "HUMAN_OVERVIEW.md"
        try:
            md = _llm_fill_human_overview(client, repo_root=repo_root)
            _write_text(hop, md)
            human_overview_updated = 1
        except Exception:
            # 不阻断 fill 主流程（避免外部 API 波动导致整体失败）
            human_overview_updated = 0

    return {
        "modules_updated": changed_modules,
        "l2_modules_updated": changed_l2,
        "l2_apis_updated": changed_l2_apis,
        "l2_flows_updated": changed_l2_flows,
        "l2_playbooks_updated": changed_l2_playbooks,
        "human_overview_updated": human_overview_updated,
        "use_llm": use_llm,
    }


def export_wiki_markdown(repo_root: Path, out_path: Path | None = None) -> Path:
    """
    生成一个单文件 Wiki 文档：docs/codewiki/WIKI.md
    """
    root = repo_root / "docs/codewiki"
    idx = _read_json(root / "L1/index.json")
    rel_ids = (idx.get("relations") or []) if isinstance(idx, dict) else []
    ep = _read_json(root / "views/entrypoints.json") if (root / "views/entrypoints.json").exists() else {}
    mf = _read_json(root / "views/module_files.json") if (root / "views/module_files.json").exists() else {}

    out_path = out_path or (root / "WIKI.md")

    lines: list[str] = []
    lines.append("# CodeWiki（自动导出）")
    lines.append("")
    lines.append("## Entrypoints")
    for e in (ep.get("entrypoints") or []):
        if not isinstance(e, dict):
            continue
        lines.append(f"- **{e.get('id')}** `{(e.get('match') or {}).get('value')}`：{e.get('summary')}")
    lines.append("")

    def _render_l1_json_section(title: str, ids: list[str], l1_dir: Path, l2_dir: Path):
        lines.append(f"## {title}")
        for _id in ids:
            p = l1_dir / Path(_id + ".json")
            if not p.exists():
                continue
            obj = _read_json(p)
            lines.append(f"### `{_id}`")
            if isinstance(obj, dict):
                if obj.get("kind") == "module":
                    lines.append(f"- 职责：{obj.get('responsibility')}")
                    # module membership（如果存在 module_files 视图）
                    try:
                        if isinstance(mf, dict):
                            mmods = mf.get("modules") or {}
                            mstat = mmods.get(_id) if isinstance(mmods, dict) else None
                            if isinstance(mstat, dict):
                                lines.append(f"- 文件数：{mstat.get('count', 0)}")
                                cbe = mstat.get("counts_by_ext") or {}
                                if isinstance(cbe, dict) and cbe:
                                    # 显示前 5 个后缀统计
                                    top = sorted(cbe.items(), key=lambda x: int(x[1]), reverse=True)[:5]
                                    lines.append("- 文件类型： " + ", ".join(f"{k}={v}" for k, v in top))
                    except Exception:
                        pass
                    deps = ((obj.get('dependencies') or {}).get('depends_on') or [])
                    lines.append(f"- 依赖：{', '.join(deps) if deps else '（无）'}")
                    inv = obj.get("invariants") or []
                    if inv:
                        lines.append("- 不变量：")
                        for x in inv[:10]:
                            lines.append(f"  - {x}")
                else:
                    lines.append(f"- 摘要：{obj.get('summary')}")

            l2p = l2_dir / Path(_id + ".md")
            if l2p.exists():
                lines.append("")
                lines.append("#### L2")
                lines.append("")
                lines.extend(_read_text(l2p).splitlines())
            lines.append("")

    _render_l1_json_section("Modules", idx.get("modules") or [], root / "L1/modules", root / "L2/modules")
    _render_l1_json_section("APIs", idx.get("apis") or [], root / "L1/apis", root / "L2/apis")
    _render_l1_json_section("Flows", idx.get("flows") or [], root / "L1/flows", root / "L2/flows")
    _render_l1_json_section("Playbooks", idx.get("playbooks") or [], root / "L1/playbooks", root / "L2/playbooks")

    # Relations（可选）：模块关系图资产（用于“理清功能关系”）
    if isinstance(rel_ids, list) and rel_ids:
        lines.append("## Relations")
        for rid in rel_ids:
            try:
                obj = _read_json(root / "L1/relations" / Path(str(rid) + ".json"))
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            lines.append(f"### {obj.get('id')}")
            lines.append(f"- 摘要：{obj.get('summary')}")
            edges = obj.get("edges") or []
            if isinstance(edges, list) and edges:
                lines.append("")
                lines.append("| from | to | type | weight |")
                lines.append("|---|---|---:|---:|")
                for e in edges[:20]:
                    if isinstance(e, dict):
                        lines.append(
                            f"| {e.get('from_module','')} | {e.get('to_module','')} | {e.get('type','')} | {e.get('weight',0)} |"
                        )
                lines.append("")

    _write_text(out_path, "\n".join(lines).rstrip() + "\n")
    return out_path
