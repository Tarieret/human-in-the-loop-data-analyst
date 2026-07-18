"""
Human-in-the-loop data analyst.

Plain-English question -> OpenAI proposes a tool call against the DuckDB
MCP server (i.e. a SQL query) -> execution pauses for human approval ->
on approval, the MCP server runs the query and the model summarizes the
result.

Architecture decision: the approval gate is implemented using the
Responses API's native `require_approval` mechanism on the MCP tool,
rather than hand-rolled by generating SQL as plain text and running it
ourselves. This means the "pause before executing" behavior is enforced
by OpenAI's infrastructure, not by application code that could be
bypassed by a bug in this script.

Run:
    streamlit run app.py

Requires:
    OPENAI_API_KEY   - your OpenAI key
    MCP_SERVER_URL   - public HTTPS URL of duckdb_server.py, e.g. an
                        ngrok URL ending in /mcp
"""

import json
import os

import streamlit as st
from openai import OpenAI

st.set_page_config(
    page_title="Human-in-the-loop Data Analyst",
    page_icon="🦆",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    .block-container { max-width: 900px; padding-top: 2.5rem; }

    .hero-title {
        font-family: 'Fraunces', serif;
        font-size: 2.1rem;
        font-weight: 600;
        color: #2b2a28;
        margin-bottom: 0.15rem;
    }
    .hero-sub {
        color: #6e6a63;
        font-size: 0.95rem;
        margin-bottom: 1.8rem;
    }
    .hero-sub code {
        background: #efece6;
        color: #854f0b;
        border-radius: 4px;
        padding: 0.1rem 0.35rem;
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.85rem;
    }

    div[data-testid="stChatMessage"] {
        background: #ffffff;
        border: 1px solid #e5e1d8;
        border-radius: 14px;
        padding: 0.3rem 0.6rem;
        box-shadow: 0 1px 3px rgba(43, 42, 40, 0.05);
    }

    .approval-card {
        background: #fbf3e4;
        border: 1px solid #f0d9a8;
        border-left: 4px solid #854f0b;
        border-radius: 10px;
        padding: 1.3rem 1.5rem;
        margin-bottom: 1rem;
    }
    .approval-card .label {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.75rem;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: #854f0b;
        font-weight: 500;
    }
    .approval-card .tool-name {
        font-family: 'Fraunces', serif;
        font-size: 1.2rem;
        color: #2b2a28;
        margin-top: 0.15rem;
    }

    div[data-testid="stExpander"] {
        border: 1px solid #e5e1d8;
        border-radius: 12px;
    }

    .stButton > button {
        border-radius: 8px;
        font-weight: 500;
    }
    .stButton > button[kind="primary"] {
        background-color: #854f0b;
        border-color: #854f0b;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #6d4009;
        border-color: #6d4009;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

MODEL = "gpt-4o-mini"
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

if not OPENAI_API_KEY:
    st.error("OPENAI_API_KEY is not set. Export it before running streamlit.")
    st.stop()
if not MCP_SERVER_URL:
    st.error(
        "MCP_SERVER_URL is not set. Run mcp_server/duckdb_server.py, "
        "tunnel it publicly (e.g. `ngrok http 8000`), and set MCP_SERVER_URL "
        "to the public URL ending in /mcp."
    )
    st.stop()

client = OpenAI(api_key=OPENAI_API_KEY)

MCP_TOOL = {
    "type": "mcp",
    "server_label": "duckdb",
    "server_url": MCP_SERVER_URL,
    # Only run_query touches data, so it's the only call worth a human's
    # time to review. list_tables/describe_table are read-only schema
    # introspection: they can't return anything beyond table/column names,
    # so gating them added clicks without adding safety.
    "require_approval": {
        "always": {"tool_names": ["run_query"]},
        "never": {"tool_names": ["list_tables", "describe_table"]},
    },
}

SYSTEM_PROMPT = (
    "You are a data analyst working against a DuckDB database exposed "
    "through MCP tools: list_tables, describe_table, run_query. "
    "Before writing a query, check the schema with describe_table if you "
    "haven't already seen it in this conversation. Only ever propose "
    "SELECT queries. After a query result comes back, explain the answer "
    "in plain English, don't just repeat the raw rows."
)

if "messages" not in st.session_state:
    st.session_state.messages = []  # display history: [{role, content}]
if "previous_response_id" not in st.session_state:
    st.session_state.previous_response_id = None
if "pending_approval" not in st.session_state:
    st.session_state.pending_approval = None  # dict or None


def process_response(response):
    """
    Walk a Responses API output, append displayable events to chat history,
    and return a pending-approval dict if the model is waiting on one.
    """
    pending = None
    for item in response.output:
        item_type = getattr(item, "type", None)

        if item_type == "mcp_approval_request":
            pending = {
                "approval_request_id": item.id,
                "name": item.name,
                "arguments": item.arguments,
                "server_label": item.server_label,
            }

        elif item_type == "mcp_call":
            # A tool call that has already been approved and executed.
            st.session_state.messages.append({
                "role": "tool",
                "name": item.name,
                "arguments": item.arguments,
                "output": item.output,
            })

        elif item_type == "message":
            for block in item.content:
                if getattr(block, "type", None) == "output_text":
                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": block.text,
                    })

    st.session_state.previous_response_id = response.id
    return pending


def ask(user_text=None, approval_response=None):
    """Send either a new user question or an approval decision."""
    kwargs = {
        "model": MODEL,
        "tools": [MCP_TOOL],
        "instructions": SYSTEM_PROMPT,
    }
    if st.session_state.previous_response_id:
        kwargs["previous_response_id"] = st.session_state.previous_response_id

    if approval_response is not None:
        kwargs["input"] = [approval_response]
    else:
        kwargs["input"] = user_text

    response = client.responses.create(**kwargs)
    pending = process_response(response)
    st.session_state.pending_approval = pending


st.markdown('<div class="hero-title">🦆 Human-in-the-loop Data Analyst</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="hero-sub">Ask a question about the Instacart dataset. '
    "Every <code>run_query</code> call stops here for approval before it executes.</div>",
    unsafe_allow_html=True,
)

for msg in st.session_state.messages:
    if msg["role"] == "assistant":
        with st.chat_message("assistant"):
            st.write(msg["content"])
    elif msg["role"] == "user":
        with st.chat_message("user"):
            st.write(msg["content"])
    elif msg["role"] == "tool":
        with st.chat_message("assistant"):
            with st.expander(f"✓ Executed · {msg['name']}"):
                st.code(json.loads(msg["arguments"]).get("sql", msg["arguments"]), language="sql")
                st.json(msg["output"] if isinstance(msg["output"], dict) else {"result": msg["output"]})

pending = st.session_state.pending_approval

if pending:
    st.markdown(
        f"""
        <div class="approval-card">
            <div class="label">Approval needed</div>
            <div class="tool-name">{pending['name']}()</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    args = pending["arguments"]
    try:
        parsed = json.loads(args)
        if "sql" in parsed:
            st.code(parsed["sql"], language="sql")
        else:
            st.json(parsed)
    except (json.JSONDecodeError, TypeError):
        st.code(args)

    col1, col2 = st.columns(2)
    if col1.button("Approve and run", type="primary"):
        ask(approval_response={
            "type": "mcp_approval_response",
            "approve": True,
            "approval_request_id": pending["approval_request_id"],
        })
        st.rerun()
    if col2.button("Reject"):
        ask(approval_response={
            "type": "mcp_approval_response",
            "approve": False,
            "approval_request_id": pending["approval_request_id"],
        })
        st.rerun()

else:
    user_text = st.chat_input("Ask a question about the data...")
    if user_text:
        st.session_state.messages.append({"role": "user", "content": user_text})
        ask(user_text=user_text)
        st.rerun()
