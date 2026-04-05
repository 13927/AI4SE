from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AiseConfig:
    """
    Phase 1 配置（MVP）：
    - roots：参与 filetree/views 的源码根目录（相对 repo 根）
    - ignore：在 scan/update/validate(diff) 中忽略的路径（glob，相对 repo 根）
    - strict_diff_gate：diff gate 严格模式（allow 未命中也作为 error）
    """

    roots: list[str] = field(default_factory=lambda: ["src"])
    ignore: list[str] = field(default_factory=lambda: [".git/**", "node_modules/**", "target/**", "build/**"])
    strict_diff_gate: bool = False
    verify_allowlist: list[str] = field(
        default_factory=lambda: [
            "pytest",
            "python -m pytest",
            "mvn test",
            "mvn -q test",
            "ctest",
            "ctest --test-dir",
            "npm test",
            "pnpm test",
            "yarn test",
        ]
    )
    # Agent policy defaults（Phase 3）
    read_scopes: list[str] = field(default_factory=lambda: ["docs/codewiki/**"])
    write_scopes: list[str] = field(default_factory=lambda: ["docs/codewiki/**"])
    forbid_write_paths: list[str] = field(default_factory=lambda: [".git/**"])
    agent_budgets: dict[str, int] = field(
        default_factory=lambda: {
            "max_tool_calls": 20,
            "max_read_calls": 8,
            "max_write_calls": 6,
            "max_verify_calls": 3,
        }
    )
    # Phase 5-B：层级依赖矩阵（layer -> allowed layers）
    layer_dependency_matrix: dict[str, list[str]] = field(
        default_factory=lambda: {
            "presentation": ["presentation", "application", "domain", "cross_cutting"],
            "application": ["application", "domain", "infrastructure", "cross_cutting"],
            "domain": ["domain", "cross_cutting"],
            "infrastructure": ["infrastructure", "domain", "cross_cutting"],
            "cross_cutting": ["cross_cutting", "domain"],
            "build": ["build", "external"],
            "external": ["external"],
            "unknown": ["presentation", "application", "domain", "infrastructure", "cross_cutting", "build", "external", "unknown"],
        }
    )
    strict_layer_gate: bool = False
    # 是否在 scan 阶段使用 LLM 自动重写 views/filetree.json（全自动治理：允许每次重写）
    # - None：自动（检测到可用模型配置则启用；否则关闭）
    # - True/False：强制开/关
    auto_partition_filetree: bool | None = None


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def load_config(repo_root: Path) -> AiseConfig:
    """
    从 repo 根目录加载 aise.yml（可选）。不存在则返回默认配置。
    """
    cfg_path = repo_root / "aise.yml"
    if not cfg_path.exists():
        return AiseConfig()

    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        return AiseConfig()

    roots = _as_list(raw.get("roots")) or ["src"]
    ignore = _as_list(raw.get("ignore")) or [".git/**", "node_modules/**"]
    strict = bool(raw.get("strictDiffGate") or raw.get("strict_diff_gate") or False)
    allowlist = _as_list(raw.get("verifyAllowlist") or raw.get("verify_allowlist"))
    if not allowlist:
        allowlist = AiseConfig().verify_allowlist
    read_scopes = _as_list(raw.get("readScopes") or raw.get("read_scopes")) or AiseConfig().read_scopes
    write_scopes = _as_list(raw.get("writeScopes") or raw.get("write_scopes")) or AiseConfig().write_scopes
    forbid_write_paths = _as_list(raw.get("forbidWritePaths") or raw.get("forbid_write_paths")) or AiseConfig().forbid_write_paths

    budgets_raw = raw.get("agentBudgets") or raw.get("agent_budgets") or {}
    budgets = dict(AiseConfig().agent_budgets)
    if isinstance(budgets_raw, dict):
        for k, v in budgets_raw.items():
            if k in budgets and isinstance(v, int) and v > 0:
                budgets[k] = v

    ldm_raw = raw.get("layerDependencyMatrix") or raw.get("layer_dependency_matrix") or {}
    ldm = dict(AiseConfig().layer_dependency_matrix)
    if isinstance(ldm_raw, dict):
        for k, v in ldm_raw.items():
            if isinstance(k, str):
                ldm[k] = _as_list(v)
    strict_layer = bool(raw.get("strictLayerGate") or raw.get("strict_layer_gate") or False)
    auto_part_raw = raw.get("autoPartitionFiletree")
    if auto_part_raw is None:
        auto_part_raw = raw.get("auto_partition_filetree")
    auto_part: bool | None
    if isinstance(auto_part_raw, bool):
        auto_part = auto_part_raw
    else:
        auto_part = None

    return AiseConfig(
        roots=roots,
        ignore=ignore,
        strict_diff_gate=strict,
        verify_allowlist=allowlist,
        read_scopes=read_scopes,
        write_scopes=write_scopes,
        forbid_write_paths=forbid_write_paths,
        agent_budgets=budgets,
        layer_dependency_matrix=ldm,
        strict_layer_gate=strict_layer,
        auto_partition_filetree=auto_part,
    )
