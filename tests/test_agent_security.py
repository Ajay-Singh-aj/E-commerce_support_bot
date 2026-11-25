"""Security tests for the ShopDesk agent. No OpenAI key or Gmail needed.

Run: uv run python tests/test_agent_security.py

The real agent is built with get_agent() (real tools, HITL + logging middleware, dynamic prompt,
SQLite checkpointer, resume_with_decision). Only the chat model and Gmail are replaced:
- agent.ChatOpenAI -> a scripted fake model that returns pre-written AIMessages / tool calls
- agent.load_gmail_tools -> one fake send_email tool
The tests write to ecommerce.db; db_init runs before and after, so the demo data ends up clean.
"""

import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="shopdesk_test_"))

# Must be set before langchain_bot modules call load_dotenv() (it never overrides existing variables)
os.environ["CHECKPOINTS_DB_PATH"] = str(TMP / "checkpoints.sqlite")
os.environ["OPENAI_API_KEY"] = "sk-test-dummy-not-a-real-key"

from langchain_core.embeddings import FakeEmbeddings  # noqa: E402
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402
from langchain_core.tools import tool  # noqa: E402

from langchain_bot import agent as agent_mod  # noqa: E402
from langchain_bot import customer_tools, db_init, rag_tool  # noqa: E402
from langchain_bot.context import SessionContext  # noqa: E402
from langchain_bot.hitl_utils import handle_interrupt  # noqa: E402

PRIYA = "priya.sharma@example.com"
DB = db_init.DB_PATH


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class ScriptedChatModel(BaseChatModel):
    """Returns the next pre-scripted AIMessage on every call."""

    script: list = []

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        message = self.script.pop(0) if self.script else AIMessage(content="(no more scripted replies)")
        return ChatResult(generations=[ChatGeneration(message=message)])


FAKE_MODEL = ScriptedChatModel()


@tool
def send_email(to: list[str], subject: str, body: str) -> str:
    """Fake Gmail send_email."""
    return f"Email sent to {to}"


async def fake_load_gmail_tools():
    return [send_email]


agent_mod.ChatOpenAI = lambda **kwargs: FAKE_MODEL
agent_mod.load_gmail_tools = fake_load_gmail_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def db(sql, params=()):
    conn = sqlite3.connect(DB)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def runtime(email=PRIYA):
    return SimpleNamespace(context=SessionContext(user_email=email, conversation_id="test"))


def call(name, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{uuid4().hex[:8]}",
                                              "type": "tool_call"}])


def ask(text, script, email=PRIYA):
    """Run one customer turn against the real agent with a scripted model. Returns (thread_id, result)."""
    conversation_id = uuid4().hex[:8]
    FAKE_MODEL.script = list(script)
    result = agent_mod.get_agent().invoke(
        {"messages": [{"role": "user", "content": text}]},
        config=agent_mod.get_thread_config(email, conversation_id),
        context=SessionContext(user_email=email, conversation_id=conversation_id),
    )
    return f"{email}:{conversation_id}", result


def tool_outputs(result):
    return [m.content for m in result["messages"] if m.type == "tool"]


RESULTS = []


def check(test_id, description, condition, detail=""):
    RESULTS.append((test_id, description, bool(condition), detail))


def run(test_id, description, fn):
    try:
        fn()
    except Exception as error:
        traceback.print_exc()
        check(test_id, description, False, f"exception: {type(error).__name__}: {error}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def t1():
    graph = agent_mod.get_agent()
    names = set(graph.nodes["tools"].bound.tools_by_name)
    banned = {"sql_db_query", "sql_db_schema", "sql_db_list_tables", "sql_db_query_checker"} & names
    expected = {"get_my_orders", "get_order_details", "get_my_returns", "get_my_tickets", "get_my_spend",
                "log_email", "cancel_order_action", "create_return_action", "search_policies", "send_email"}
    check("T1", "agent has no free-form SQL tools", not banned and expected <= names,
          f"tools: {sorted(names)}" + (f" | banned found: {banned}" if banned else ""))


def t2():
    out = customer_tools.get_order_details.func(order_id=1, runtime=runtime())
    ok = '"items"' in out and '"payments"' in out and "Voltcart PowerCube 20000" in out and '"PAID"' in out
    check("T2", "get_order_details: Priya's order 1 -> items + payments", ok, out.replace("\n", " ")[:90] + "...")


def t3():
    owner = db("SELECT u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = 2")[0][0]
    out = customer_tools.get_order_details.func(order_id=2, runtime=runtime())
    check("T3", "get_order_details: order 2 (Rahul's) as Priya -> not found",
          owner != PRIYA and out == f"Order #2 was not found for {PRIYA}.", f"owner={owner} | {out}")


def t4():
    rt = runtime()
    outputs = [
        customer_tools.get_my_orders.func(runtime=rt),
        customer_tools.get_my_orders.func(runtime=rt, status="delivered", limit=2),
        *[customer_tools.get_order_details.func(order_id=i, runtime=rt) for i in range(1, 21)],
        customer_tools.get_my_returns.func(runtime=rt),
        customer_tools.get_my_tickets.func(runtime=rt),
        customer_tools.get_my_spend.func(runtime=rt),
    ]
    leaks = [o for o in outputs if "pbkdf2" in o.lower() or "password" in o.lower()]
    other_emails = [o for o in outputs if "rahul" in o.lower() or "ananya" in o.lower()]
    check("T4", "no customer tool output contains password hashes or other customers",
          not leaks and not other_emails, f"{len(outputs)} outputs checked")


def t5():
    conn = customer_tools._connect_read_only()
    try:
        conn.execute("UPDATE orders SET status = 'CANCELLED' WHERE id = 13")
        conn.commit()
        refused, detail = False, "UPDATE succeeded"
    except sqlite3.OperationalError as error:
        refused, detail = True, str(error)
    finally:
        conn.close()
    still = db("SELECT status FROM orders WHERE id = 13")[0][0]
    check("T5", "read-only connection refuses writes", refused and still == "PLACED", f"{detail} | order 13 {still}")


def t6():
    priya_id = db("SELECT id FROM users WHERE email = ?", (PRIYA,))[0][0]
    count = lambda: db("SELECT COUNT(*) FROM email_logs")[0][0]  # noqa: E731
    before = count()
    ok_msg = customer_tools.log_email.func(subject="Order #13 cancellation received", body_preview="x" * 150,
                                           email_type="cancel_request", runtime=runtime())
    after_ok = count()
    row = db("SELECT user_id, email, email_type, LENGTH(body_preview) FROM email_logs ORDER BY id DESC LIMIT 1")[0]
    bad_msg = customer_tools.log_email.func(subject="x", body_preview="y", email_type="DROP TABLE users",
                                            runtime=runtime())
    after_bad = count()
    ok = (after_ok == before + 1 and row == (priya_id, PRIYA, "CANCEL_REQUEST", 100)
          and bad_msg.startswith("Invalid email_type") and after_bad == after_ok)
    check("T6", "log_email: one row for Priya; invalid email_type rejected", ok,
          f"row={row} | invalid -> '{bad_msg[:40]}...'")


STATE = {}


def t7():
    thread, result = ask("Cancel order 13", [
        call("get_order_details", order_id=13),
        call("cancel_order_action", order_id=13),
    ])
    STATE["t7_thread"] = thread
    interrupted = bool(result.get("__interrupt__"))
    status = db("SELECT status FROM orders WHERE id = 13")[0][0]
    message = handle_interrupt(result, thread, PRIYA)
    pending = db("SELECT action_type, order_id, status FROM pending_actions WHERE thread_id = ?", (thread,))
    check("T7", "'Cancel order 13' -> HITL interrupt, still PLACED, pending_actions saved",
          interrupted and status == "PLACED" and pending == [("CANCEL_ORDER", 13, "PENDING")] and message,
          f"interrupt={interrupted} | order 13 {status} | pending={pending}")


def t8():
    FAKE_MODEL.script = [AIMessage(content="Your order 13 is cancelled.")]
    result = agent_mod.resume_with_decision(STATE["t7_thread"], "approve")
    order = db("SELECT status FROM orders WHERE id = 13")[0][0]
    payments = {r[0] for r in db("SELECT status FROM payments WHERE order_id = 13")}
    ticket = db("SELECT COUNT(*) FROM tickets WHERE subject = 'Cancellation of order #13'")[0][0]
    check("T8", "approve -> order 13 CANCELLED, payment REFUNDED, ticket created",
          order == "CANCELLED" and payments == {"REFUNDED"} and ticket == 1,
          f"order={order} payments={payments} tickets={ticket} | {tool_outputs(result)[-1][:50]}")


def t9():
    before = db("SELECT * FROM returns")
    thread, result = ask("Return the desk lamp from order 4, it flickers", [
        call("get_order_details", order_id=4),
        call("create_return_action", order_id=4, product_name="Voltcart LED Desk Lamp", reason="Flickers"),
    ])
    interrupted = bool(result.get("__interrupt__"))
    handle_interrupt(result, thread, PRIYA)
    FAKE_MODEL.script = [AIMessage(content="Your return was rejected.")]
    out = agent_mod.resume_with_decision(thread, "reject", message="Test rejection")
    after = db("SELECT * FROM returns")
    check("T9", "return on order 4, admin rejects -> no new returns row",
          interrupted and after == before and "REJECTED" in tool_outputs(out)[-1],
          f"returns {len(before)} -> {len(after)}")


def t10():
    owner = db("SELECT u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = 14")[0][0]
    thread, result = ask("Cancel order 14", [call("cancel_order_action", order_id=14)])
    interrupted = bool(result.get("__interrupt__"))
    FAKE_MODEL.script = [AIMessage(content="That order is not on your account.")]
    out = agent_mod.resume_with_decision(thread, "approve")
    status = db("SELECT status FROM orders WHERE id = 14")[0][0]
    payment = db("SELECT status FROM payments WHERE order_id = 14")[0][0]
    tool_result = tool_outputs(out)[-1]
    check("T10", "Priya cancels Rahul's order 14, admin approves -> still PLACED",
          owner != PRIYA and interrupted and status == "PLACED" and payment == "PAID" and "not found" in tool_result,
          f"owner={owner} | order 14 {status}/{payment} | {tool_result}")


def t11():
    """Bug 2: the same item cannot be returned (and refunded) twice."""
    outputs = []
    for _ in range(2):
        thread, result = ask("Return the wireless charging pad from order 4", [
            call("create_return_action", order_id=4, product_name="Voltcart Wireless Charging Pad", reason="Not needed"),
        ])
        FAKE_MODEL.script = [AIMessage(content="ok")]
        outputs.append(tool_outputs(agent_mod.resume_with_decision(thread, "approve"))[-1])
    rows = db("""SELECT COUNT(*) FROM returns r JOIN order_items oi ON oi.id = r.order_item_id
                 JOIN products p ON p.id = oi.product_id WHERE r.order_id = 4 AND p.name = 'Voltcart Wireless Charging Pad'""")[0][0]
    check("T11", "same item returned twice -> 2nd refused, one refund only",
          "APPROVED" in outputs[0] and "already has return" in outputs[1] and rows == 1,
          f"returns for item: {rows} | 2nd: {outputs[1][:60]}")


def t12():
    """Bug 3: the agent still builds when the Gmail MCP server cannot start."""
    async def broken_gmail():
        raise RuntimeError("OAuth keys file not found")

    agent_mod.load_gmail_tools = broken_gmail
    try:
        graph = agent_mod.create_support_agent()
        names = set(graph.nodes["tools"].bound.tools_by_name)
        email_flag = agent_mod.EMAIL_AVAILABLE
    finally:
        agent_mod.load_gmail_tools = fake_load_gmail_tools
        agent_mod.EMAIL_AVAILABLE = True
    check("T12", "Gmail unavailable -> agent still builds, no send_email, prompt flag off",
          "send_email" not in names and "get_order_details" in names and email_flag is False,
          f"EMAIL_AVAILABLE={email_flag} | {len(names)} tools")


def _stop_chroma():
    try:
        import gc

        from chromadb.api.shared_system_client import SharedSystemClient

        for system in list(SharedSystemClient._identifier_to_system.values()):
            system.stop()
        SharedSystemClient.clear_system_cache()
        gc.collect()
    except Exception as error:  # cleanup only; never fail the test run here
        print(f"(could not stop Chroma cleanly: {error})")


def t13():
    """Bug 1: an empty chroma_db/ left by a failed build is rebuilt, not reused."""
    original_dir, original_embeddings = rag_tool.CHROMA_DIR, rag_tool.OpenAIEmbeddings
    rag_tool.CHROMA_DIR = TMP / "chroma_db"
    rag_tool.OpenAIEmbeddings = lambda **kwargs: FakeEmbeddings(size=16)
    rag_tool._vector_store = None
    try:
        empty = rag_tool.Chroma(persist_directory=str(rag_tool.CHROMA_DIR), embedding_function=FakeEmbeddings(size=16))
        empty_count = empty._collection.count()
        has_files = any(rag_tool.CHROMA_DIR.iterdir())
        store = rag_tool.get_vector_store()
        count = store._collection.count()
    finally:
        rag_tool.CHROMA_DIR, rag_tool.OpenAIEmbeddings = original_dir, original_embeddings
        rag_tool._vector_store = None
        _stop_chroma()  # Windows can't delete the temp folder while Chroma holds its sqlite file open
    check("T13", "empty chroma_db/ from a failed build gets rebuilt",
          has_files and empty_count == 0 and count > 0, f"before: files={has_files}, docs=0 | after: docs={count}")


# ---------------------------------------------------------------------------

def main():
    db_init.init_database()
    tests = [
        ("T1", t1), ("T2", t2), ("T3", t3), ("T4", t4), ("T5", t5), ("T6", t6), ("T7", t7),
        ("T8", t8), ("T9", t9), ("T10", t10), ("T11", t11), ("T12", t12), ("T13", t13),
    ]
    try:
        for test_id, fn in tests:
            run(test_id, fn.__name__, fn)
    finally:
        db_init.init_database()  # leave clean demo data behind
        if agent_mod._checkpointer is not None:
            agent_mod._checkpointer.conn.close()
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n" + "=" * 110)
    print(f"{'ID':4} {'RESULT':6}  {'TEST':62} DETAIL")
    print("-" * 110)
    for test_id, description, passed, detail in RESULTS:
        print(f"{test_id:4} {'PASS' if passed else 'FAIL':6}  {description[:62]:62} {detail[:140]}")
    failed = [r for r in RESULTS if not r[2]]
    print("-" * 110)
    print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} passed. Demo database re-initialised.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
