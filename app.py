"""Customer chat app (Streamlit).

Run: uv run streamlit run app.py
"""

import json
from pathlib import Path
from uuid import uuid4

import streamlit as st
from dotenv import load_dotenv

from langchain_bot.agent import get_agent, get_thread_config
from langchain_bot.auth import authenticate_user, lockout_minutes_left
from langchain_bot.context import SessionContext
from langchain_bot.hitl_utils import handle_interrupt, has_pending_action_for_thread
from langchain_bot.llm import describe_provider
from langchain_bot.rag_tool import initialize_vector_store

THREADS_FILE = Path(__file__).resolve().parent / "chat_threads.json"
MAX_MESSAGES_PER_SESSION = 30  # caps model cost and abuse per browser session


# ---------------------------------------------------------------------------
# Conversation ids per user (ids only - messages live in the checkpointer)
# ---------------------------------------------------------------------------

def load_all_threads():
    if THREADS_FILE.exists():
        return json.loads(THREADS_FILE.read_text(encoding="utf-8"))
    return {}


def load_threads(user_email):
    return load_all_threads().get(user_email, [])


def save_threads(data):
    THREADS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def add_thread(user_email, conversation_id):
    data = load_all_threads()
    data.setdefault(user_email, []).append({"id": conversation_id, "label": f"Chat {conversation_id[:8]}"})
    save_threads(data)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

def init_session():
    st.session_state.setdefault("user_email", None)
    st.session_state.setdefault("user_role", None)
    st.session_state.setdefault("conversation_id", None)
    st.session_state.setdefault("vector_store_ready", False)
    st.session_state.setdefault("message_count", 0)


def start_new_conversation():
    conversation_id = str(uuid4())
    add_thread(st.session_state.user_email, conversation_id)
    st.session_state.conversation_id = conversation_id


def get_thread_id():
    return f"{st.session_state.user_email}:{st.session_state.conversation_id}"


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def render_history():
    config = get_thread_config(st.session_state.user_email, st.session_state.conversation_id)
    snapshot = get_agent().get_state(config)

    for msg in snapshot.values.get("messages", []):
        if msg.type == "human":
            with st.chat_message("user"):
                st.markdown(msg.content)
        elif msg.type == "ai" and msg.content:
            with st.chat_message("assistant"):
                st.markdown(msg.content)

    if has_pending_action_for_thread(get_thread_id()):
        st.info("⏳ Your request in this conversation is pending admin review.")


def chat_round(user_input):
    thread_id = get_thread_id()

    if has_pending_action_for_thread(thread_id):
        st.warning("A request in this conversation is already waiting for admin review. "
                   "Please start a new conversation for other questions.")
        return

    st.session_state.message_count += 1
    result = get_agent().invoke(
        {"messages": [{"role": "user", "content": user_input}]},
        config=get_thread_config(st.session_state.user_email, st.session_state.conversation_id),
        context=SessionContext(
            user_email=st.session_state.user_email,
            conversation_id=st.session_state.conversation_id,
            role=st.session_state.user_role,
        ),
    )

    handle_interrupt(result, thread_id, st.session_state.user_email)
    st.rerun()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(page_title="ShopDesk AI · Customer Support", page_icon="🛒")
    st.title("ShopDesk AI")
    st.caption("24×7 order, return and policy support · demo store: Voltcart")
    st.caption(f"Model: {describe_provider()}")
    load_dotenv()
    init_session()

    if not st.session_state.vector_store_ready:
        with st.spinner("Initializing knowledge base..."):
            try:
                initialize_vector_store()
                get_agent()
                st.session_state.vector_store_ready = True
            except Exception as error:
                st.error(f"Initialization failed: {error}")
                st.stop()

    # Login
    if not st.session_state.user_email:
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            role = st.selectbox("Role", ["customer", "admin"])
            submitted = st.form_submit_button("Login")

        if submitted:
            user = authenticate_user(email, password, role)
            if user:
                st.session_state.user_email = user["email"]
                st.session_state.user_role = user["role"]
                threads = load_threads(user["email"])
                if threads:
                    st.session_state.conversation_id = threads[0]["id"]
                else:
                    start_new_conversation()
                st.success("Logged in")
                st.rerun()
            elif lockout_minutes_left(email):
                st.error(f"Too many failed logins. Try again in {lockout_minutes_left(email)} minutes.")
            else:
                st.error("Invalid email or password")
        return

    # Sidebar: logout + conversations
    with st.sidebar:
        if st.button("Logout"):
            st.session_state.clear()
            st.rerun()
        st.header("Conversations")
        if st.button("Start new conversation"):
            start_new_conversation()
            st.rerun()

        threads = load_threads(st.session_state.user_email)
        ids = [t["id"] for t in threads]
        labels = {t["id"]: t["label"] for t in threads}
        selected = st.selectbox(
            "Select conversation",
            ids,
            index=ids.index(st.session_state.conversation_id),
            format_func=lambda conv_id: labels[conv_id],
        )
        if selected != st.session_state.conversation_id:
            st.session_state.conversation_id = selected
            st.rerun()

    st.info(
        f"Logged in as {st.session_state.user_email} ({st.session_state.user_role}) · "
        f"Conversation: {st.session_state.conversation_id}"
    )

    render_history()

    limit_reached = st.session_state.message_count >= MAX_MESSAGES_PER_SESSION
    if limit_reached:
        st.info(f"You've reached the limit of {MAX_MESSAGES_PER_SESSION} messages for this session. "
                "Thanks for chatting! For anything else, please contact our support team.")
    prompt = st.chat_input("Ask a question", disabled=limit_reached)
    if prompt and not limit_reached:
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                chat_round(prompt)


if __name__ == "__main__":
    main()
