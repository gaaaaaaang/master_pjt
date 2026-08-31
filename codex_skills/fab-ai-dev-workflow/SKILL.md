---
name: fab-ai-dev-workflow
description: Guide development of the FAB AI Assistant project from SMT2020 data loading through DB APIs, sub-agents, RAG with Milvus, and LangGraph orchestration.
metadata:
  short-description: FAB AI Assistant development workflow
---

# FAB AI Assistant Development Workflow

Use this skill when working in the `master_pjt` repository on the FAB AI Assistant. It tells Codex which project documents are the source of truth so implementation work follows the agreed order and scenarios.

## Project Intent

Build an AI chat assistant for FAB operations that answers production-status questions, diagnoses process issues, estimates impact, and compares trends using SMT2020 process data and semiconductor process knowledge.

## Required Project Docs

At the start of implementation work, read these repository documents as needed:

- `docs/development_todo.md` = source of truth for development order and immediate next steps.
- `docs/user_scenarios.md` = source of truth for scenario behavior and acceptance criteria.
- `docs/architecture.md` = source of truth for current system structure.

Follow `docs/development_todo.md` unless the user explicitly asks for a different task:

1. Validate loaded process data and schema.
2. Build read-only DB access and query validation.
3. Implement basic FAB query APIs without LLM.
4. Implement template-based Text2SQL sub-agent.
5. Build RAG ingestion and retrieval with Milvus.
6. Connect LangGraph nodes and state.
7. Wire the frontend chat UI.
8. Add scenario-based tests and safety checks.

Use `docs/user_scenarios.md` when implementing or testing behavior for:

- SC-001 current status lookup
- SC-002 root cause diagnosis
- SC-003 impact analysis
- SC-004 trend and comparison queries

## Local Conventions

- Use `sub_agent` as the package name for external capability modules.
- Keep detailed agent behavior stubbed until the corresponding data path is working.
- Prefer SQL templates and allowlists before free-form SQL generation.
- Use Milvus as the Vector DB.
- Use `text-embedding-3-large` for RAG embeddings unless the user prioritizes lower cost.
- Do not implement direct equipment control or automatic production actions.
- Every answer should expose evidence and limitations when available.

## First Question To Ask Yourself

Before coding, identify the current phase:

- If DB tables are not verified, inspect schema and row counts first.
- If DB access is not implemented, build the read-only connector first.
- If APIs are not returning real DB data, implement those before LangGraph.
- If APIs work, connect sub-agents and then LangGraph.
