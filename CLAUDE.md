# CLAUDE.md - homeflix

Project guidance and architecture for Claude Code.

## Architecture & Overview
- **Name:** homeflix
- **Workspace:** /home/azureuser/workspace/active/homeflix
- **Environment:** Ubuntu Linux (vm-runtime-01)

## Development Workflow
- Follow test-driven development (TDD) for new features and bugfixes.
- Run tests and verifications before committing.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Setup (graphify-out/ is gitignored, so each machine builds its own graph):
- If `graphify` is missing: `uv tool install graphifyy` (needs uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- If graphify-out/graph.json is missing: run `graphify update .` (AST-only, no LLM cost), then optionally `graphify label . --backend=claude-cli` to give communities readable names (uses LLM).
- `.graphifyignore` (gitignore syntax) excludes `tests/` so god nodes reflect production code. To look at tests, read them directly.

Rules:
- When graphify-out/graph.json exists, run `graphify query "<question>"` before your first grep, search or source-file read for a codebase question, and put this rule in every subagent prompt that explores code. Read raw files only for lines the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
- graphify-out/ is not committed; never add it to git.
