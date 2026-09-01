from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx
import pandas as pd
import streamlit as st

DEFAULT_BACKEND_URL = "http://localhost:8000"


def build_payload(message: str, fab: str, line: str, process: str) -> dict[str, str]:
    payload: dict[str, str] = {"message": message}
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
        with st.expander("Agent 실행 로그", expanded=True):
            for event in events:
                node = event.get("node", "unknown")
                event_type = event.get("type", "event")
                text = event.get("message", "")
                st.markdown(f"**{node}** · `{event_type}`  \n{text}")
                sql = (event.get("data") or {}).get("sql")
                if sql:
                    st.code(sql, language="sql")
                sample_rows = (event.get("data") or {}).get("sample_rows")
                if sample_rows:
                    st.caption("sample rows")
                    st.dataframe(sample_rows, use_container_width=True, hide_index=True)

    if message.get("sql"):
        with st.expander("생성 SQL", expanded=True):
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


def reset_chat() -> None:
    st.session_state["messages"] = []
    st.session_state.pop("conversation_id", None)


st.set_page_config(page_title="FAB Assistant", page_icon="F", layout="wide")

st.markdown(
    """
    <style>
    :root { --green:#16805f; --green-dark:#0f6048; --ink:#1d2a26; --muted:#7a8782; --line:#e6ece9; --soft:#f4f8f6; }
    #MainMenu, footer { visibility:hidden; }
    header { visibility:hidden; height:0; }
    .block-container { max-width:none; padding:1.5rem 2rem 6rem; }
    [data-testid="stSidebar"] { border-right:1px solid var(--line); }
    [data-testid="stSidebar"] > div:first-child { padding-top:1.25rem; }
    [data-testid="stChatMessage"] { padding:.7rem 0; }
    [data-testid="stChatMessage"] p { line-height:1.6; }
    [data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] { border-color:var(--line); }
    .brand { display:flex; align-items:center; gap:.65rem; padding:.3rem 0 1.6rem; }
    .brand-mark { display:grid; place-items:center; width:2.25rem; height:2.25rem; border-radius:.7rem; background:var(--green); color:white; font-weight:800; }
    .brand-title { color:var(--ink); font-size:1rem; font-weight:750; }
    .brand-subtitle { color:var(--muted); font-size:.74rem; margin-top:.12rem; }
    .eyebrow { color:var(--green); font-size:.72rem; font-weight:800; letter-spacing:.1em; text-transform:uppercase; }
    .page-title { color:var(--ink); font-size:1.55rem; font-weight:750; letter-spacing:-.04em; margin-top:.15rem; }
    .panel-title { color:var(--ink); font-size:.82rem; font-weight:750; letter-spacing:.01em; }
    .panel-caption { color:var(--muted); font-size:.75rem; }
    .list-item { border-bottom:1px solid var(--line); padding:.8rem .15rem; }
    .list-item strong { color:var(--ink); font-size:.82rem; }
    .list-item span { color:var(--muted); display:block; font-size:.73rem; margin-top:.2rem; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .dot { display:inline-block; width:.45rem; height:.45rem; border-radius:50%; background:#35a477; margin-right:.35rem; }
    .welcome { padding:5rem 1rem 3rem; text-align:center; }
    .welcome h1 { color:var(--ink); font-size:2.1rem; letter-spacing:-.055em; margin-bottom:.55rem; }
    .welcome p { color:var(--muted); }
    .empty-icon { display:grid; place-items:center; width:3.25rem; height:3.25rem; border-radius:1rem; background:#e5f4ed; color:var(--green); font-size:1.5rem; margin:0 auto 1rem; }
    .detail-row { border-bottom:1px solid var(--line); padding:.7rem 0; }
    .detail-label { color:var(--muted); font-size:.72rem; }
    .detail-value { color:var(--ink); font-size:.82rem; font-weight:650; margin-top:.2rem; }
    .status { color:var(--green); font-size:.75rem; font-weight:700; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "messages" not in st.session_state:
    st.session_state["messages"] = []

with st.sidebar:
    st.markdown(
        '<div class="brand"><div class="brand-mark">F</div><div><div class="brand-title">FAB Assistant</div><div class="brand-subtitle">Operations workspace</div></div></div>',
        unsafe_allow_html=True,
    )
    if st.button("＋  새 대화", use_container_width=True):
        reset_chat()
        st.rerun()

    st.divider()
    st.caption("WORKSPACE")
    st.markdown("⌂  Overview")
    st.markdown("▣  Conversations")
    st.markdown("◌  Analytics")
    st.divider()
    st.caption("FAB CONTEXT")
    fab = st.text_input("Fab", placeholder="FAB-A")
    line = st.text_input("Line", placeholder="M2")
    process = st.text_input("Process", placeholder="CMP")
    st.divider()
    st.caption("CONNECTION")
    backend_url = st.text_input(
        "FastAPI URL",
        value=os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL),
        label_visibility="collapsed",
    )
    st.caption("● LangGraph / Text2SQL / PostgreSQL connected")

st.markdown('<div class="eyebrow">FAB OPERATIONS</div><div class="page-title">Customer conversations</div>', unsafe_allow_html=True)
st.write("")

left, center, right = st.columns([0.8, 1.8, 0.82], gap="large")

with left:
    st.markdown('<div class="panel-title">Inbox</div><div class="panel-caption">Your active workspace</div>', unsafe_allow_html=True)
    st.text_input("Search", placeholder="Search conversations", label_visibility="collapsed")
    st.markdown('<div class="list-item"><strong><span class="dot"></span>All conversations</strong><span>Ready for your next query</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="list-item"><strong>Assigned to me</strong><span>0 conversations</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="list-item"><strong>Unassigned</strong><span>0 conversations</span></div>', unsafe_allow_html=True)
    st.write("")
    st.markdown('<div class="panel-title">Status</div>', unsafe_allow_html=True)
    st.markdown('<div class="list-item"><strong><span class="dot"></span>Trace mode</strong><span>Streaming agent events</span></div>', unsafe_allow_html=True)

with center:
    st.markdown('<div class="panel-title">FAB Assistant <span class="status">● Online</span></div><div class="panel-caption">Ask about your fab, line, or process</div>', unsafe_allow_html=True)
    st.divider()
    messages: list[dict[str, str]] = st.session_state["messages"]
    if not messages:
        st.markdown(
            '<div class="welcome"><div class="empty-icon">✦</div><h1>무엇을 도와드릴까요?</h1><p>FAB 운영 데이터를 기반으로 질문을 시작해보세요.</p></div>',
            unsafe_allow_html=True,
        )
    for item in messages:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])
            if item["role"] == "assistant" and item.get("limitations"):
                with st.expander("현재 제한사항"):
                    for limitation in item["limitations"].split("\n"):
                        st.write(f"- {limitation}")
            if item["role"] == "assistant":
                render_agent_artifacts(item)

with right:
    st.markdown('<div class="panel-title">Conversation details</div><div class="panel-caption">Selected workspace context</div>', unsafe_allow_html=True)
    st.divider()
    details = [("FAB", fab or "Not set"), ("Line", line or "Not set"), ("Process", process or "Not set"), ("Channel", "FAB Assistant")]
    for label, value in details:
        st.markdown(f'<div class="detail-row"><div class="detail-label">{label}</div><div class="detail-value">{value}</div></div>', unsafe_allow_html=True)
    st.write("")
    st.markdown('<div class="panel-title">Notes</div>', unsafe_allow_html=True)
    st.caption("질문 실행 시 Planner, Supervisor, Text2SQL, Visualization 단계가 스트림으로 표시됩니다.")

prompt = st.chat_input("FAB 운영에 대해 질문해보세요...")
if prompt:
    clean_prompt = prompt.strip()
    if clean_prompt:
        messages.append({"role": "user", "content": clean_prompt})
        payload = build_payload(clean_prompt, fab.strip(), line.strip(), process.strip())
        try:
            events: list[dict[str, Any]] = []
            final_data: dict[str, Any] = {}
            with st.chat_message("assistant"):
                status = st.status("Agent 실행 중...", expanded=True)
                for event in stream_backend(backend_url, payload):
                    events.append(event)
                    status.write(
                        f"{event.get('node', 'unknown')} · {event.get('message', '')}"
                    )
                    if (event.get("data") or {}).get("sql"):
                        status.code(event["data"]["sql"], language="sql")
                    if event.get("type") == "run_completed":
                        final_data = event.get("data") or {}
                status.update(label="Agent 실행 완료", state="complete", expanded=False)
                st.markdown(final_data.get("answer", "응답이 없습니다."))
                render_agent_artifacts(
                    {
                        "events": events,
                        "sql": final_data.get("sql"),
                        "chart": final_data.get("chart"),
                    }
                )
            st.session_state["conversation_id"] = final_data.get("conversation_id")
            messages.append(
                {
                    "role": "assistant",
                    "content": final_data.get("answer", "응답이 없습니다."),
                    "limitations": "\n".join(final_data.get("limitations") or []),
                    "events": events,
                    "sql": final_data.get("sql"),
                    "chart": final_data.get("chart"),
                }
            )
        except httpx.HTTPError as exc:
            messages.append({"role": "assistant", "content": f"백엔드에 연결하지 못했습니다.\n\n`{exc}`"})
        st.rerun()
