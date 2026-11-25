"""Admin dashboard (Streamlit).

Run: uv run streamlit run app_admin.py --server.port 8502
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from langchain_bot.agent import get_agent, resume_with_decision
from langchain_bot.auth import authenticate_user
from langchain_bot.rag_tool import initialize_vector_store

DB_PATH = Path(__file__).resolve().parent / "ecommerce.db"


def query(sql, params=()):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    conn.close()
    return rows


def update_pending_status(pending_id, status):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE pending_actions SET status = ?, updated_at = ? WHERE id = ?",
        (status, datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), pending_id),
    )
    conn.commit()
    conn.close()


def apply_decision(row, decision, edited_args=None, message=""):
    with st.spinner("Resuming the customer's conversation..."):
        result = resume_with_decision(row["thread_id"], decision, edited_args, message)
    new_status = "REJECTED" if decision == "reject" else "APPROVED"
    update_pending_status(row["id"], new_status)
    st.session_state.last_result = f"Request #{row['id']} {new_status}. Agent: {result['messages'][-1].content}"
    st.rerun()


def show_pending_actions():
    st.subheader("Return / cancel requests")
    col1, col2 = st.columns(2)
    status = col1.selectbox("Status", ["PENDING", "APPROVED", "REJECTED", "ALL"])
    email = col2.text_input("Customer email (optional)")

    sql = "SELECT * FROM pending_actions WHERE 1 = 1"
    params = []
    if status != "ALL":
        sql += " AND status = ?"
        params.append(status)
    if email:
        sql += " AND user_email = ?"
        params.append(email.strip())
    sql += " ORDER BY created_at DESC"

    rows = query(sql, params)
    if not rows:
        st.info("No requests to review.")
        return

    for row in rows:
        with st.container(border=True):
            st.write(
                f"**#{row['id']} · {row['action_type']}** · Order #{row['order_id']} · "
                f"{row['user_email']} · **{row['status']}**"
            )
            st.caption(f"Thread: {row['thread_id']} · Created: {row['created_at']}")
            if row["action_type"] == "CREATE_RETURN":
                st.write(f"Product: {row['product_name']} · Reason: {row['reason']}")

            if row["status"] != "PENDING":
                continue

            edited_args = None
            if row["action_type"] == "CREATE_RETURN":
                product = st.text_input("Product name", row["product_name"] or "", key=f"product_{row['id']}")
                reason = st.text_input("Reason", row["reason"] or "", key=f"reason_{row['id']}")
                edited_args = {"product_name": product, "reason": reason}
            reject_note = st.text_input("Rejection reason (optional)", key=f"note_{row['id']}")

            col1, col2, col3 = st.columns(3)
            if col1.button("Approve", key=f"approve_{row['id']}"):
                apply_decision(row, "approve")
            if edited_args and col2.button("Edit & approve", key=f"edit_{row['id']}"):
                apply_decision(row, "edit", edited_args=edited_args)
            if col3.button("Reject", key=f"reject_{row['id']}"):
                apply_decision(row, "reject", message=reject_note)


def show_tickets():
    st.subheader("Tickets")
    col1, col2, col3 = st.columns(3)
    status = col1.selectbox("Ticket status", ["ALL", "OPEN", "IN_REVIEW", "RESOLVED", "CLOSED"])
    email = col2.text_input("User email (optional)", key="ticket_email")
    from_date = col3.date_input("Created from", date(2026, 1, 1))

    sql = """
        SELECT t.id, t.subject, t.status, t.user_email, r.order_id, r.status AS return_status,
               t.thread_id AS conversation_id, t.created_at, t.updated_at
        FROM tickets t LEFT JOIN returns r ON r.id = t.return_id
        WHERE t.created_at >= ?
    """
    params = [from_date.isoformat()]
    if status != "ALL":
        sql += " AND t.status = ?"
        params.append(status)
    if email:
        sql += " AND t.user_email = ?"
        params.append(email.strip())
    sql += " ORDER BY t.created_at DESC"

    rows = query(sql, params)
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("No tickets found.")


def main():
    st.set_page_config(page_title="ShopDesk AI · Admin", page_icon="🛠️", layout="wide")
    st.title("ShopDesk AI · Admin Console")
    load_dotenv()
    st.session_state.setdefault("admin_email", None)
    st.session_state.setdefault("services_ready", False)
    st.session_state.setdefault("last_result", None)

    if not st.session_state.admin_email:
        with st.form("admin_login"):
            email = st.text_input("Admin email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
        if submitted:
            if authenticate_user(email, password, "admin"):
                st.session_state.admin_email = email.strip()
                st.rerun()
            else:
                st.error("Invalid admin login")
        return

    if not st.session_state.services_ready:
        with st.spinner("Loading services (RAG + Gmail) for the agent..."):
            try:
                initialize_vector_store()
                get_agent()
            except Exception as error:
                st.error(f"Initialization failed: {error}")
                st.stop()
        st.session_state.services_ready = True

    st.caption(f"Logged in as {st.session_state.admin_email}")
    if st.button("Refresh"):
        st.rerun()

    if st.session_state.last_result:
        st.success(st.session_state.last_result)
        st.session_state.last_result = None

    show_pending_actions()
    st.divider()
    show_tickets()


if __name__ == "__main__":
    main()
