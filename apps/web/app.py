from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import httpx
import pandas as pd
import streamlit as st

DEFAULT_BACKEND_URL = "http://localhost:8000"


def build_payload(
    message: str,
    fab: str,
    line: str,
    process: str,
    conversation_id: str | None,
) -> dict[str, str]:
    payload: dict[str, str] = {"message": message}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    for key, value in {"fab": fab, "line": line, "process": process}.items():
        if value:
            payload[key] = value
    return payload


def ask_backend(base_url: str, payload: dict[str, str]) -> dict[str, Any]:
    response = httpx.post(f"{base_url.rstrip('/')}/api/chat", json=payload, timeout=30.0)
    response.raise_for_status()
    return response.json()


def stream_backend(base_url: str, payload: dict[str, str]) -> Iterator[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/chat/stream"
    event_name = ""
    data_lines: list[str] = []
    with httpx.stream("POST", url, json=payload, timeout=60.0) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line == "":
                if data_lines:
                    payload_text = "\n".join(data_lines)
                    event_payload = json.loads(payload_text)
                    if event_name:
                        event_payload["sse_event"] = event_name
                    yield event_payload
                event_name = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())


def render_agent_artifacts(message: dict[str, Any]) -> None:
    events = message.get("events") or []
    if events:
        with st.expander("Agent 생각 및 실행 로그", expanded=True):
            for event in events:
                node = event.get("node", "unknown")
                event_type = event.get("type", "event")
                text = event.get("message", "")
                st.markdown(f"**{node}** · `{event_type}`  \n{text}")
                reasoning = ((event.get("data") or {}).get("reasoning") or {}).get(
                    "summary"
                )
                if reasoning:
                    st.caption(f"생각: {reasoning}")
                render_tool_result(event)

    if message.get("sql"):
        with st.expander("최종 SQL", expanded=True):
            st.code(message["sql"], language="sql")

    chart = message.get("chart")
    if chart:
        st.caption(chart.get("title", "Visualization"))
        rows = chart.get("rows") or []
        encoding = chart.get("encoding") or {}
        x_field = (encoding.get("x") or {}).get("field")
        y_field = (encoding.get("y") or {}).get("field")
        if rows and x_field and y_field:
            frame = pd.DataFrame(rows)
            st.line_chart(frame, x=x_field, y=y_field, use_container_width=True)
            st.dataframe(frame, use_container_width=True, hide_index=True)


def render_tool_result(event: dict[str, Any]) -> None:
    data = event.get("data") or {}
    node = event.get("node")
    if node != "text2sql" and not data.get("query_plan") and not data.get("sample_rows"):
        return

    st.caption("Tool 결과")
    status = data.get("status")
    row_count = data.get("row_count")
    columns = data.get("columns")
    if status or row_count is not None or columns:
        cols = st.columns(3)
        cols[0].caption("status")
        cols[0].write(f"**{status or '-'}**")
        cols[1].caption("rows")
        cols[1].write(f"**{row_count if row_count is not None else '-'}**")
        cols[2].caption("columns")
        cols[2].write(f"**{len(columns) if columns else 0}**")

    sql = data.get("sql")
    if sql:
        st.code(sql, language="sql")

    query_plan = data.get("query_plan")
    if query_plan:
        with st.expander("query plan", expanded=False):
            st.json(query_plan)

    sample_rows = data.get("sample_rows")
    if sample_rows:
        st.dataframe(sample_rows, use_container_width=True, hide_index=True)


def reset_chat() -> None:
    conversation = active_conversation()
    conversation["messages"] = []
    conversation["backend_id"] = None
    conversation["title"] = "New FAB conversation"
    st.session_state.pop("pending_prompt", None)


def queue_prompt() -> None:
    prompt = st.session_state.get("chat_prompt", "").strip()
    if prompt:
        st.session_state["pending_prompt"] = prompt


def make_conversation(messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    title = "New FAB conversation"
    if messages:
        first_user = next((item["content"] for item in messages if item["role"] == "user"), "")
        if first_user:
            title = first_user[:48]
    return {
        "id": uuid4().hex,
        "title": title,
        "backend_id": st.session_state.get("conversation_id"),
        "messages": messages or [],
    }


def ensure_conversation_state() -> None:
    if "conversations" in st.session_state:
        return
    previous_messages = st.session_state.get("messages") or []
    conversation = make_conversation(previous_messages)
    st.session_state["conversations"] = [conversation]
    st.session_state["active_conversation_id"] = conversation["id"]


def active_conversation() -> dict[str, Any]:
    ensure_conversation_state()
    active_id = st.session_state.get("active_conversation_id")
    conversations = st.session_state["conversations"]
    for conversation in conversations:
        if conversation["id"] == active_id:
            return conversation
    st.session_state["active_conversation_id"] = conversations[0]["id"]
    return conversations[0]


def create_new_conversation() -> None:
    conversation = make_conversation()
    st.session_state["conversations"].insert(0, conversation)
    st.session_state["active_conversation_id"] = conversation["id"]
    st.session_state.pop("pending_prompt", None)


def active_context_label(fab: str, line: str, process: str) -> str:
    values = [value for value in [fab, line, process] if value]
    return " / ".join(values) if values else "No context selected"


def collect_artifacts(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        if message.get("sql"):
            artifacts.append({"kind": "SQL", "title": "Final SQL", "payload": message["sql"]})
        chart = message.get("chart")
        if chart:
            artifacts.append(
                {
                    "kind": "Chart",
                    "title": chart.get("title", "Visualization"),
                    "payload": chart,
                }
            )
        for event in message.get("events") or []:
            data = event.get("data") or {}
            if data.get("sample_rows"):
                artifacts.append(
                    {
                        "kind": "Data",
                        "title": f"{event.get('node', 'tool')} sample rows",
                        "payload": data["sample_rows"],
                    }
                )
            if data.get("query_plan"):
                artifacts.append(
                    {
                        "kind": "Plan",
                        "title": f"{event.get('node', 'tool')} query plan",
                        "payload": data["query_plan"],
                    }
                )
    return artifacts


def render_artifact(artifact: dict[str, Any]) -> None:
    st.markdown(
        f'<div class="artifact-card"><div class="artifact-kind">{artifact["kind"]}</div>'
        f'<div class="artifact-title">{artifact["title"]}</div></div>',
        unsafe_allow_html=True,
    )
    payload = artifact["payload"]
    if artifact["kind"] == "SQL":
        st.code(payload, language="sql")
    elif artifact["kind"] == "Chart":
        rows = payload.get("rows") or []
        encoding = payload.get("encoding") or {}
        x_field = (encoding.get("x") or {}).get("field")
        y_field = (encoding.get("y") or {}).get("field")
        if rows and x_field and y_field:
            frame = pd.DataFrame(rows)
            st.line_chart(frame, x=x_field, y=y_field, use_container_width=True)
    elif artifact["kind"] == "Data":
        st.dataframe(payload, use_container_width=True, hide_index=True)
    else:
        st.json(payload)


def queue_suggested_prompt(prompt: str) -> None:
    st.session_state["pending_prompt"] = prompt


st.set_page_config(page_title="FAB Assistant", page_icon="F", layout="wide")
st.info(
    "현재 화면은 기존 Streamlit UI입니다. 고도화된 프런트엔드는 React에서 실행됩니다. "
    "저장소 루트의 별도 터미널에서 `npm --prefix apps/web run dev`를 실행한 뒤 "
    "[새 FAB 채팅 화면](http://localhost:5173/)을 열어 주세요. "
    "백엔드는 별도 터미널에서 "
    "`PYTHONPATH=apps/assistant/src uv run uvicorn app.main:app --reload`로 실행합니다."
)
ensure_conversation_state()

st.markdown(
    """
    <style>
    :root {
      --green:#16805f;
      --green-dark:#0f6048;
      --mint:#dff3eb;
      --ink:#18231f;
      --muted:#74817c;
      --line:#e3e9e6;
      --soft:#f6f8f5;
      --panel:rgba(255,255,255,.84);
      --shadow:0 22px 70px rgba(27,42,36,.08);
    }
    #MainMenu, footer { visibility:hidden; }
    header { visibility:hidden; height:0; }
    .stApp {
      background:
        radial-gradient(circle at 18% 8%, rgba(236,198,170,.35), transparent 26rem),
        radial-gradient(circle at 82% 0%, rgba(206,234,222,.48), transparent 24rem),
        linear-gradient(135deg, #fbfaf7 0%, #f5f8f5 55%, #fff8f2 100%);
    }
    .block-container { max-width:none; padding:1.1rem 1.35rem 4.2rem; }
    [data-testid="stChatMessage"] {
      background:rgba(255,255,255,.74);
      border:1px solid rgba(227,233,230,.9);
      border-radius:1.1rem;
      box-shadow:0 10px 30px rgba(24,35,31,.04);
      margin:.8rem 0;
      padding:.85rem 1rem;
    }
    [data-testid="stChatMessage"] p { line-height:1.6; }
    [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); }
    [data-testid="column"]:has(.history-anchor),
    [data-testid="column"]:has(.chat-anchor),
    [data-testid="column"]:has(.artifact-anchor) {
      background:var(--panel);
      border:1px solid rgba(227,233,230,.88);
      border-radius:1.4rem;
      box-shadow:var(--shadow);
      min-height:calc(100vh - 8rem);
      padding:1.05rem;
      backdrop-filter:blur(18px);
    }
    [data-testid="column"]:has(.chat-anchor) { background:rgba(255,255,255,.62); }
    .shell-title {
      align-items:center;
      display:flex;
      justify-content:space-between;
      margin-bottom:1rem;
      padding:.15rem .25rem;
    }
    .brand { align-items:center; display:flex; gap:.7rem; }
    .brand-mark {
      display:grid;
      place-items:center;
      width:2.25rem;
      height:2.25rem;
      border-radius:.8rem;
      background:linear-gradient(135deg, var(--green), #22a976);
      color:white;
      font-weight:850;
      box-shadow:0 12px 28px rgba(22,128,95,.22);
    }
    .brand-title { color:var(--ink); font-size:1rem; font-weight:800; }
    .brand-subtitle { color:var(--muted); font-size:.74rem; margin-top:.08rem; }
    .top-status {
      align-items:center;
      background:rgba(255,255,255,.68);
      border:1px solid var(--line);
      border-radius:999px;
      color:var(--muted);
      display:flex;
      font-size:.74rem;
      gap:.45rem;
      padding:.48rem .75rem;
    }
    .eyebrow { color:var(--green); font-size:.68rem; font-weight:850; letter-spacing:.1em; text-transform:uppercase; }
    .page-title { color:var(--ink); font-size:1.3rem; font-weight:800; letter-spacing:0; margin-top:.1rem; }
    .panel-title { color:var(--ink); font-size:.82rem; font-weight:750; letter-spacing:.01em; }
    .panel-caption { color:var(--muted); font-size:.75rem; }
    .section-kicker { color:var(--green); font-size:.68rem; font-weight:850; letter-spacing:.08em; margin-bottom:.35rem; text-transform:uppercase; }
    .side-panel, .artifact-panel, .chat-surface { min-height:calc(100vh - 10rem); }
    .list-item { border-bottom:1px solid var(--line); padding:.8rem .15rem; }
    .list-item strong { color:var(--ink); font-size:.82rem; }
    .list-item span { color:var(--muted); display:block; font-size:.73rem; margin-top:.2rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .dot { display:inline-block; width:.45rem; height:.45rem; border-radius:50%; background:#35a477; margin-right:.35rem; }
    .history-meta { color:var(--muted); font-size:.72rem; margin-top:.15rem; }
    .welcome { min-height:23rem; padding:4rem 1rem 2.4rem; text-align:center; }
    .welcome h1 { color:var(--ink); font-size:2.25rem; letter-spacing:0; line-height:1.12; margin-bottom:.65rem; }
    .welcome p { color:var(--muted); font-size:.95rem; }
    .empty-icon {
      display:grid;
      place-items:center;
      width:4rem;
      height:4rem;
      border-radius:1.3rem;
      background:
        radial-gradient(circle at 35% 20%, #fff6e8, transparent 38%),
        linear-gradient(135deg, #e5f4ed, #f8ebe2);
      color:var(--green);
      font-size:1.7rem;
      margin:0 auto 1.15rem;
      box-shadow:0 18px 44px rgba(22,128,95,.13);
    }
    .prompt-strip { display:grid; gap:.7rem; grid-template-columns:repeat(3,minmax(0,1fr)); margin:0 auto 1.3rem; max-width:48rem; }
    .prompt-card {
      background:rgba(255,255,255,.72);
      border:1px solid var(--line);
      border-radius:1rem;
      color:var(--ink);
      min-height:6.4rem;
      padding:.9rem;
      text-align:left;
    }
    .prompt-card strong { display:block; font-size:.82rem; margin-bottom:.35rem; }
    .prompt-card span { color:var(--muted); font-size:.74rem; line-height:1.4; }
    .detail-row { border-bottom:1px solid var(--line); padding:.7rem 0; }
    .detail-label { color:var(--muted); font-size:.72rem; }
    .detail-value { color:var(--ink); font-size:.82rem; font-weight:650; margin-top:.2rem; }
    .status { color:var(--green); font-size:.75rem; font-weight:700; }
    .artifact-card {
      background:linear-gradient(180deg, rgba(255,255,255,.9), rgba(250,252,250,.78));
      border:1px solid var(--line);
      border-radius:1rem;
      margin-top:.8rem;
      padding:.85rem;
    }
    .artifact-kind { color:var(--green); font-size:.68rem; font-weight:800; letter-spacing:.08em; text-transform:uppercase; }
    .artifact-title { color:var(--ink); font-size:.82rem; font-weight:750; line-height:1.35; margin-top:.2rem; }
    .context-pill {
      background:rgba(223,243,235,.66);
      border:1px solid rgba(22,128,95,.18);
      border-radius:.9rem;
      color:var(--green-dark);
      font-size:.78rem;
      font-weight:750;
      margin:.75rem 0 1rem;
      padding:.7rem .8rem;
    }
    div.stButton > button {
      border-radius:.9rem;
      border:1px solid var(--line);
      min-height:2.55rem;
    }
    div.stButton > button:hover {
      border-color:rgba(22,128,95,.35);
      color:var(--green-dark);
    }
    div[data-testid="stChatInput"] {
      border-radius:1.2rem;
      box-shadow:0 16px 50px rgba(24,35,31,.1);
    }
    @media (max-width: 980px) {
      .prompt-strip { grid-template-columns:1fr; }
      [data-testid="column"]:has(.history-anchor),
      [data-testid="column"]:has(.chat-anchor),
      [data-testid="column"]:has(.artifact-anchor) {
        min-height:auto;
      }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div class="shell-title"><div class="brand"><div class="brand-mark">F</div>'
    '<div><div class="brand-title">FAB Assistant</div>'
    '<div class="brand-subtitle">Operations AI workspace</div></div></div>'
    '<div><div class="eyebrow">FAB OPERATIONS</div>'
    '<div class="page-title">Customer conversations</div></div>'
    '<div class="top-status"><span class="dot"></span> LangGraph online</div></div>',
    unsafe_allow_html=True,
)

left, center, right = st.columns([0.72, 1.85, 0.88], gap="large")
conversation = active_conversation()

with right:
    st.markdown('<span class="artifact-anchor"></span>', unsafe_allow_html=True)
    st.markdown('<div class="section-kicker">Context</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-title">Workspace context</div><div class="panel-caption">Applied to this conversation</div>', unsafe_allow_html=True)
    fab = st.text_input("Fab", placeholder="FAB-A", key="fab")
    line = st.text_input("Line", placeholder="M2", key="line")
    process = st.text_input("Process", placeholder="CMP", key="process")
    backend_url = st.text_input(
        "FastAPI URL",
        value=os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL),
        key="backend_url",
    )
    st.markdown(
        f'<div class="context-pill">{active_context_label(fab, line, process)}</div>',
        unsafe_allow_html=True,
    )
    st.caption("LangGraph / Text2SQL / PostgreSQL connected")
    st.divider()
    st.markdown('<div class="section-kicker">Vault</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-title">Generated artifacts</div><div class="panel-caption">Saved from agent tools in this chat</div>', unsafe_allow_html=True)
    artifacts = collect_artifacts(conversation["messages"])
    if artifacts:
        st.caption(f"{len(artifacts)} saved artifact(s)")
        for artifact in artifacts:
            render_artifact(artifact)
    else:
        st.caption("아직 생성된 SQL, 차트, 데이터 결과가 없습니다.")

with left:
    st.markdown('<span class="history-anchor"></span>', unsafe_allow_html=True)
    st.markdown('<div class="section-kicker">History</div>', unsafe_allow_html=True)
    if st.button("＋  새 대화", use_container_width=True):
        create_new_conversation()
        st.rerun()
    st.write("")
    st.markdown('<div class="panel-title">Chat history</div><div class="panel-caption">Previous FAB conversations</div>', unsafe_allow_html=True)
    history_query = st.text_input("Search conversations", placeholder="Search...", label_visibility="collapsed")
    visible_conversations = [
        item
        for item in st.session_state["conversations"]
        if not history_query or history_query.lower() in item["title"].lower()
    ]
    for item in visible_conversations:
        label = item["title"] or "New FAB conversation"
        prefix = "● " if item["id"] == conversation["id"] else ""
        if st.button(f"{prefix}{label}", key=f"history-{item['id']}", use_container_width=True):
            st.session_state["active_conversation_id"] = item["id"]
            st.session_state.pop("pending_prompt", None)
            st.rerun()
        st.markdown(
            f'<div class="history-meta">{len(item["messages"])} messages</div>',
            unsafe_allow_html=True,
        )
    st.divider()
    st.markdown('<div class="panel-title">Mode</div>', unsafe_allow_html=True)
    st.markdown('<div class="list-item"><strong><span class="dot"></span>Trace mode</strong><span>Planner, supervisor, tool events</span></div>', unsafe_allow_html=True)

with center:
    st.markdown('<span class="chat-anchor"></span>', unsafe_allow_html=True)
    st.markdown('<div class="section-kicker">Chat canvas</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="panel-title">FAB Assistant <span class="status">● Online</span></div>'
        '<div class="panel-caption">Ask about your fab, line, process, or generated artifacts</div>',
        unsafe_allow_html=True,
    )
    st.divider()
    messages: list[dict[str, Any]] = conversation["messages"]
    pending_prompt = st.session_state.get("pending_prompt", "")
    if not messages and not pending_prompt:
        st.markdown(
            '<div class="welcome"><div class="empty-icon">✦</div><h1>무엇을 도와드릴까요?</h1>'
            '<p>FAB 운영 데이터를 기반으로 질문하면 실행 로그와 산출물이 자동 정리됩니다.</p></div>',
            unsafe_allow_html=True,
        )
        prompt_cols = st.columns(3)
        suggestions = [
            ("Status lookup", "fab10 Dry_Etch toolgroup 목록 보여줘"),
            ("Trend chart", "fab10의 lotrelease를 날짜 기준 라인차트로 그려줘"),
            ("Root cause", "최근 공정 지연 원인을 설비/라인 기준으로 요약해줘"),
        ]
        for column, (title, prompt_text) in zip(prompt_cols, suggestions, strict=False):
            with column:
                st.markdown(
                    f'<div class="prompt-card"><strong>{title}</strong>'
                    f'<span>{prompt_text}</span></div>',
                    unsafe_allow_html=True,
                )
                if st.button("Use prompt", key=f"suggest-{title}", use_container_width=True):
                    queue_suggested_prompt(prompt_text)
                    st.rerun()
    for item in messages:
        with st.chat_message(item["role"]):
            if item["role"] == "assistant":
                render_agent_artifacts(item)
                st.markdown("#### 최종 답변")
                st.markdown(item["content"])
                if item.get("limitations"):
                    with st.expander("현재 제한사항"):
                        for limitation in item["limitations"].split("\n"):
                            st.write(f"- {limitation}")
            else:
                st.markdown(item["content"])

    if pending_prompt:
        clean_prompt = pending_prompt.strip()
        st.session_state.pop("pending_prompt", None)
        if clean_prompt:
            messages.append({"role": "user", "content": clean_prompt})
            if conversation["title"] == "New FAB conversation":
                conversation["title"] = clean_prompt[:48]
            payload = build_payload(
                clean_prompt,
                fab.strip(),
                line.strip(),
                process.strip(),
                conversation.get("backend_id"),
            )
            try:
                events: list[dict[str, Any]] = []
                final_data: dict[str, Any] = {}
                with st.chat_message("user"):
                    st.markdown(clean_prompt)
                with st.chat_message("assistant"):
                    status = st.status("Agent 실행 중...", expanded=True)
                    for event in stream_backend(backend_url, payload):
                        events.append(event)
                        with status:
                            st.write(
                                f"{event.get('node', 'unknown')} · {event.get('message', '')}"
                            )
                            reasoning = (
                                ((event.get("data") or {}).get("reasoning") or {}).get(
                                    "summary"
                                )
                            )
                            if reasoning:
                                st.caption(f"생각: {reasoning}")
                            if (event.get("data") or {}).get("sql"):
                                st.code(event["data"]["sql"], language="sql")
                            sample_rows = (event.get("data") or {}).get("sample_rows")
                            if sample_rows:
                                st.dataframe(
                                    sample_rows,
                                    use_container_width=True,
                                    hide_index=True,
                                )
                        if event.get("type") == "run_completed":
                            final_data = event.get("data") or {}
                    status.update(label="Agent 실행 완료", state="complete", expanded=False)
                    render_agent_artifacts(
                        {
                            "events": events,
                            "sql": final_data.get("sql"),
                            "chart": final_data.get("chart"),
                        }
                    )
                    st.markdown("#### 최종 답변")
                    st.markdown(final_data.get("answer", "응답이 없습니다."))
                conversation["backend_id"] = final_data.get("conversation_id")
                messages.append(
                    {
                        "role": "assistant",
                        "content": final_data.get("answer", "응답이 없습니다."),
                        "limitations": "\n".join(final_data.get("limitations") or []),
                        "events": events,
                        "sql": final_data.get("sql"),
                        "chart": final_data.get("chart"),
                        "reasoning_state": final_data.get("reasoning_state") or [],
                    }
                )
            except httpx.HTTPError as exc:
                messages.append({"role": "assistant", "content": f"백엔드에 연결하지 못했습니다.\n\n`{exc}`"})
            st.rerun()

    st.chat_input(
        "FAB 운영에 대해 질문해보세요...",
        key="chat_prompt",
        on_submit=queue_prompt,
    )
