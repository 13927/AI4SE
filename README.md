# aise — Executable CodeWiki Agent (prototype)

> 一句话：把大型代码仓库“自动整理成可校验的 Wiki”，让人和 Agent 都能更快理解、定位、改动与验证。

![CI](https://github.com/13927/AI4SE/actions/workflows/selftest.yml/badge.svg)

## 这是什么

aise 是一个 **CLI coding agent（原型）**，核心能力是为任意 git 仓库生成/维护一套 **Executable CodeWiki**（落盘到 `docs/codewiki/`）：
- **L1**：结构化主事实源（可校验）
- **Views**：导航/证据视图（从入口和文件快速定位）
- **L2**：语义解释（可选，配合 LLM 生成）

目标：让“读懂一个项目/评估改动影响/生成修改指南”从靠经验变成可回归流程。

## 核心命令

- `aise init`：初始化 `docs/codewiki/` 骨架与 schema
- `aise scan`：抽取入口/模块/证据视图（可选 LLM 自动划分模块边界）
- `aise validate`：schema + 规则校验（可做 CI 门禁）
- `aise fill --use-llm`：生成/补齐 L2，并生成 `HUMAN_OVERVIEW.md`
- `aise export --strict`：导出 `WIKI.md`，并做 “0 占位” 严格门禁
- `aise agent`：交互式 REPL（wiki-first；写入需确认）
- `aise agent-run`：非交互式集成运行（CI 回归）

## 快速开始（本地）

```bash
python -m pip install -e ".[dev]"

cd <your-repo>
aise init --command-name app
aise scan
aise validate --mode static --format text
aise export --out docs/codewiki/WIKI.md --strict
```

## LLM 配置（OpenAI 兼容）

### 方式 A：环境变量（CI/脚本推荐）

```bash
export AISE_OPENAI_BASE_URL="https://api.openai.com/v1"
export AISE_OPENAI_API_KEY="sk-..."
export AISE_OPENAI_MODEL="gpt-4.1-mini"
```

### 方式 B：本机全局加密存储（开发机推荐）

```bash
export AISE_CRED_PASSPHRASE="你的本机口令（建议 20+ 位随机）"
aise auth-set --base-url "https://api.openai.com/v1" --api-key "sk-..." --model "gpt-4.1-mini"
aise auth-status
```

## 全自动模块划分（LLM 重写 filetree）

在目标仓库根目录 `aise.yml` 打开：

```yaml
autoPartitionFiletree: true
```

效果：
- `aise scan` 会让 LLM **全量重写** `docs/codewiki/views/filetree.json`（模块边界规则）
- 并据此刷新 `module_files/module_symbols/java_http_routes/entry_graph` 等视图，保证最终产物一致

## 全仓符号索引（函数/全局变量可定位）

`aise scan` 还会生成：
- `docs/codewiki/views/symbol_index.jsonl`

其中每一行是一条 symbol 记录（JSONL），包含：
- `file + range`（行列范围）
- `kind`（function/method/class/global_var…）
- `module`（由 filetree best-match 推导的主归属模块）

## License

MIT
