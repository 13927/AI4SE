from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from .codewiki_ops import (
    init_repo,
    scan_repo,
    update_repo,
    validate_views,
    validate_l1_static,
    validation_report,
)
from .agent_runtime import run_agent_repl
from .agent_runner import load_scenario, run_agent_noninteractive
from .llm_openai import OpenAIClient, OpenAIConfig, load_openai_config
from .credentials import (
    clear_openai_api_key,
    get_openai_api_key,
    get_openai_api_key_encrypted,
    keyring_status,
    load_global_openai_profile,
    save_global_openai_profile,
    set_openai_api_key,
    set_openai_api_key_encrypted,
)
from .selftest import run_selftest
from .wiki_complete import export_wiki_markdown, fill_repo_codewiki, maybe_use_llm_default, count_placeholders


app = typer.Typer(add_completion=False, help="aise: AI4SE coding agent prototype (CodeWiki-first).")
console = Console()


@app.command("init")
def cmd_init(
    command_name: str = typer.Option(
        "app",
        "--command-name",
        help="你的项目主 CLI 命令名（写入 views/entrypoints.json 的默认规则）。",
    )
):
    """
    初始化：若无 git 则 git init；创建 docs/codewiki 骨架 + views + 最小 schema。
    """
    cwd = Path.cwd()
    init_repo(cwd, command_name=command_name)
    console.print("[green]已初始化 docs/codewiki/（并确保已 git init）。[/green]")
    console.print("下一步：运行 [bold]aise scan[/bold] 生成更细的 views 映射。")


@app.command("scan")
def cmd_scan():
    """
    冷启动扫描（MVP）：启发式生成/更新 views（先粗后细，便于维护）。
    """
    cwd = Path.cwd()
    scan_repo(cwd)
    console.print("[green]scan 完成：已生成/更新 docs/codewiki/views/*[/green]")


@app.command("update")
def cmd_update(
    base: str = typer.Option("HEAD~1", "--base", help="git base ref（默认 HEAD~1）"),
    head: str = typer.Option("HEAD", "--head", help="git head ref（默认 HEAD）"),
):
    """
    增量更新（MVP）：基于 git diff 映射受影响模块，并刷新已有 module 条目的 provenance。
    """
    cwd = Path.cwd()
    affected = update_repo(cwd, base=base, head=head)
    console.print(f"[green]update 完成[/green]：受影响模块 {len(affected)} 个")
    if affected:
        for m in sorted(affected):
            console.print(f" - {m}")


@app.command("validate")
def cmd_validate(
    mode: str = typer.Option("static", "--mode", help="static|diff（MVP 先实现 static）"),
    format: str = typer.Option("json", "--format", help="json|text"),
    base: str = typer.Option("HEAD~1", "--base", help="diff 模式下使用的 base ref（默认 HEAD~1）"),
    head: str = typer.Option("HEAD", "--head", help="diff 模式下使用的 head ref（默认 HEAD）"),
):
    """
    校验（MVP）：先校验 views 的 schema（后续扩展到 L1 全规则集）。
    """
    cwd = Path.cwd()
    findings = []
    if mode not in ("static", "diff"):
        raise typer.BadParameter("mode 必须是 static 或 diff")

    findings.extend(validate_views(cwd))
    if mode == "static":
        findings.extend(validate_l1_static(cwd))
    else:
        from .codewiki_ops import validate_l1_diff

        findings.extend(validate_l1_static(cwd))
        findings.extend(validate_l1_diff(cwd, base=base, head=head))

    report = validation_report(findings)
    if format == "json":
        console.print_json(json.dumps(report, ensure_ascii=False))
    else:
        console.print(f"errors={report['summary']['errors']} warnings={report['summary']['warnings']} infos={report['summary']['infos']}")
        for f in report["findings"]:
            console.print(f"[{f['severity']}] {f['rule_id']} {f['target']} {f['path']} - {f['message']}")


def main():
    app()


@app.command("agent")
def cmd_agent(
    command_name: str = typer.Option(
        "app",
        "--command-name",
        help="你的项目主 CLI 命令名（当需要自动 init/scan 时会用到）。",
    ),
    read_scope: list[str] = typer.Option(
        ["docs/codewiki/**"],
        "--read-scope",
        help="允许 read_file 读取的路径范围（glob，可重复传参）。默认仅 docs/codewiki/**。",
    ),
    max_read_calls: int = typer.Option(8, "--max-read-calls", help="每个任务允许 read_file 的最大次数（预算）。"),
    max_write_calls: int = typer.Option(6, "--max-write-calls", help="每个任务允许 write_file 的最大次数（预算）。"),
    max_tool_calls: int = typer.Option(20, "--max-tool-calls", help="每个任务允许的工具调用总次数（预算）。"),
    max_verify_calls: int = typer.Option(3, "--max-verify-calls", help="每个任务允许 run_verify 的最大次数（预算）。"),
    compact_threshold_messages: int = typer.Option(60, "--compact-threshold-messages", help="消息条数超过该阈值后触发压缩（Phase 3，v1 预留）。"),
):
    """
    启动 REPL（OpenAI 兼容接口）：wiki-first，写入需确认。
    """
    run_agent_repl(
        command_name=command_name,
        read_scopes=list(read_scope),
        max_tool_calls=max_tool_calls,
        max_read_calls=max_read_calls,
        max_write_calls=max_write_calls,
        max_verify_calls=max_verify_calls,
        compact_threshold_messages=compact_threshold_messages,
    )


@app.command("agent-run")
def cmd_agent_run(
    repo: Path = typer.Option(Path("."), "--repo", help="目标仓库路径（默认当前目录）。"),
    task: str = typer.Option("", "--task", help="要执行的任务文本（与 --scenario 二选一）。"),
    scenario: Path | None = typer.Option(None, "--scenario", help="场景 JSON 文件（包含 task + approvals）。"),
    max_steps: int = typer.Option(80, "--max-steps", help="最大 tool loop 步数（防死循环）。"),
):
    """
    非交互式 agent integration runner（CI 用）：
    - 自动审批（policy 驱动）
    - 产出审计日志 .aise/logs/session-*.jsonl
    """
    if scenario is None and not task.strip():
        raise typer.BadParameter("必须提供 --task 或 --scenario")
    sc = load_scenario(scenario) if scenario is not None else None
    t = sc.task if sc is not None else task
    code = run_agent_noninteractive(repo_root=repo, task=t, max_steps=max_steps, scenario=sc)
    raise typer.Exit(code=code)


@app.command("api-test")
def cmd_api_test(
    prompt: str = typer.Option("请回复：API 连接正常", "--prompt", help="用于测试的提示词"),
    base_url: str = typer.Option("", "--base-url", help="覆盖 AISE_OPENAI_BASE_URL（可选）"),
    api_key: str = typer.Option("", "--api-key", help="覆盖 AISE_OPENAI_API_KEY（可选）"),
    model: str = typer.Option("", "--model", help="覆盖 AISE_OPENAI_MODEL（可选）"),
):
    """
    测试 OpenAI 兼容接口是否可用（最小请求：chat/completions）。
    """
    if base_url or api_key or model:
        cfg = OpenAIConfig(
            base_url=base_url or (os.environ.get("AISE_OPENAI_BASE_URL") or "https://api.openai.com/v1"),
            api_key=api_key or (os.environ.get("AISE_OPENAI_API_KEY") or ""),
            model=model or (os.environ.get("AISE_OPENAI_MODEL") or "gpt-4.1-mini"),
        )
        if not cfg.api_key:
            raise typer.BadParameter("缺少 api_key：请传 --api-key 或设置 AISE_OPENAI_API_KEY")
    else:
        cfg = load_openai_config()

    client = OpenAIClient(cfg)
    console.print(Panel.fit(f"base_url={cfg.base_url}\nmodel={cfg.model}", title="aise api-test"))
    try:
        resp = client.chat_completions(
            messages=[
                {"role": "system", "content": "你是一个用于连通性测试的助手。"},
                {"role": "user", "content": prompt},
            ]
        )
        console.print(Syntax(json.dumps(resp, ensure_ascii=False, indent=2), "json", word_wrap=True))
    except httpx.HTTPStatusError as e:
        text = ""
        try:
            text = e.response.text
        except Exception:
            text = "<no response text>"
        console.print(f"[red]HTTP {e.response.status_code}[/red] {e.request.method} {e.request.url}")
        console.print(Syntax(text[:4000], "json", word_wrap=True))
        raise typer.Exit(code=2)
    except httpx.HTTPError as e:
        console.print(f"[red]HTTPError[/red] {type(e).__name__}: {e}")
        raise typer.Exit(code=2)
    finally:
        client.close()


@app.command("auth-set")
def cmd_auth_set(
    base_url: str = typer.Option(..., "--base-url", help="OpenAI 兼容接口 base_url（例如 https://api.xxx/v1）"),
    api_key: str = typer.Option(..., "--api-key", help="API key（将写入系统钥匙串，不写入项目）"),
    model: str = typer.Option("gpt-4.1-mini", "--model", help="默认模型（写入全局 profile）"),
):
    """
    将 OpenAI 兼容接口配置写入本机：
    - api_key 写入系统钥匙串（keyring）
    - base_url/model 写入 ~/.config/aise/openai.yaml（非敏感）
    """
    st = keyring_status()
    ok = False
    if st.get("available"):
        try:
            set_openai_api_key(base_url=base_url, api_key=api_key)
            ok = True
        except Exception:
            ok = False
    if not ok:
        # keyring 在部分环境不可用：降级到本地加密文件（需要 AISE_CRED_PASSPHRASE）
        set_openai_api_key_encrypted(base_url=base_url, api_key=api_key)
    save_global_openai_profile(base_url=base_url, model=model)
    console.print(Panel.fit(f"base_url={base_url}\nmodel={model}\nkeyring={st.get('backend')}", title="aise auth-set"))


@app.command("auth-status")
def cmd_auth_status(
    base_url: str = typer.Option("", "--base-url", help="指定 base_url（可选；为空则读取 load_openai_config 的 base_url）"),
):
    """
    查看本机 OpenAI 配置状态（不会打印 api_key 明文）。
    """
    st = keyring_status()
    bu = base_url
    md = ""
    # 优先展示全局 profile（即使没有 api_key 也能看到 base_url/model）
    gp = load_global_openai_profile()
    if gp and not bu:
        bu = gp.base_url
    if gp and not md:
        md = gp.model
    # 如果能完整加载（包含 api_key），则覆盖展示
    try:
        cfg = load_openai_config()
        bu = bu or cfg.base_url
        md = md or cfg.model
    except Exception:
        # 没有 api_key/口令时允许失败，status 仍然可用
        pass
    has_key = False
    if bu:
        has_key = bool(get_openai_api_key(base_url=bu)) or bool(get_openai_api_key_encrypted(base_url=bu))
    console.print_json(
        json.dumps(
            {
                "keyring": st,
                "base_url": bu,
                "model": md,
                "has_api_key": has_key,
            },
            ensure_ascii=False,
        )
    )


@app.command("auth-clear")
def cmd_auth_clear(
    base_url: str = typer.Option(..., "--base-url", help="要清除的 base_url（对应钥匙串 entry）"),
):
    """
    清除指定 base_url 的 api_key（从系统钥匙串删除）。
    """
    clear_openai_api_key(base_url=base_url)
    console.print(f"[green]已清除钥匙串中的 api_key：{base_url}[/green]")


@app.command("models")
def cmd_models():
    """
    查询该 OpenAI 兼容接口可用模型（若提供方支持 /models）。
    """
    cfg = load_openai_config()
    client = OpenAIClient(cfg)
    try:
        r = client._client.get("/models")
        r.raise_for_status()
        obj = r.json()
        console.print(Syntax(json.dumps(obj, ensure_ascii=False, indent=2), "json", word_wrap=True))
    except Exception as e:
        console.print(f"[yellow]该接入地址可能不支持 /models：{type(e).__name__}: {e}[/yellow]")
        raise typer.Exit(code=2)
    finally:
        client.close()


@app.command("selftest")
def cmd_selftest(
    workdir: str = typer.Option(
        "/tmp/aise-selftest",
        "--workdir",
        help="自测工作目录（会删除并重建子目录）。",
    )
):
    """
    自动下载示例仓库并跑 init/scan/validate/update/diff-validate，用于 CI/长期自动执行。
    不依赖真实 API key。
    """
    run_selftest(Path(workdir))
    console.print("[green]selftest 完成：所有目标仓库通过。[/green]")


@app.command("fill")
def cmd_fill(
    use_llm: Optional[bool] = typer.Option(
        None,
        "--use-llm/--no-llm",
        help="是否使用 LLM 来补全文档（当前版本主要用启发式，LLM 预留）。默认：如果存在 AISE_OPENAI_API_KEY 则启用。",
    ),
):
    """
    补齐 docs/codewiki 中的占位内容（不会覆盖非占位内容）。
    """
    cwd = Path.cwd()
    use_llm2 = maybe_use_llm_default() if use_llm is None else bool(use_llm)
    rep = fill_repo_codewiki(cwd, use_llm=use_llm2)
    console.print_json(json.dumps(rep, ensure_ascii=False))


@app.command("export")
def cmd_export(
    out: str = typer.Option("docs/codewiki/WIKI.md", "--out", help="导出单文件 Wiki 的输出路径。"),
    strict: bool = typer.Option(False, "--strict", help="硬标准：导出后若仍检测到占位，则退出码非 0。"),
):
    """
    导出单文件 Wiki（Markdown）：默认写入 docs/codewiki/WIKI.md。
    """
    cwd = Path.cwd()
    p = export_wiki_markdown(cwd, out_path=cwd / out)
    text = p.read_text(encoding="utf-8", errors="replace")
    n = count_placeholders(text)
    console.print(f"[green]已导出：{p}[/green] placeholders={n}")
    if strict and n > 0:
        raise typer.Exit(code=2)


if __name__ == "__main__":
    main()
