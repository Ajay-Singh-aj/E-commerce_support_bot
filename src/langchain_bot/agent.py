"""The ShopDesk support agent.

Tools: search_policies (RAG) + customer data tools + send_email (Gmail MCP, customer-only) + cancel/return action tools.
There is no free-form SQL: every data tool is scoped in code to the logged-in customer.
Also: SQLite checkpointer, human-in-the-loop middleware, logging middleware.
"""

import asyncio
import os
import sqlite3

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, dynamic_prompt
from langchain.mcp import MCPAdapter
from langchain.tools import ToolRuntime, tool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from langchain_bot.action_tools import get_action_tools
from langchain_bot.context import SessionContext
from langchain_bot.customer_tools import get_customer_tools
from langchain_bot.llm import describe_provider, get_chat_model, get_retry_middleware
from langchain_bot.middleware import get_logging_middleware
from langchain_bot.rag_tool import search_policies

load_dotenv()

# ---------------------------------------------------------------------------
# Gmail MCP server
# ---------------------------------------------------------------------------

GMAIL_MCP_CONFIG = {
    "mcpServers": {
        "gmail": {
            "command": "npx",
            "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"],
        },
    }
}


async def load_gmail_tools():
    async with MCPAdapter(GMAIL_MCP_CONFIG) as adapter:
        return await adapter.list_tools()


# False when the Gmail MCP server could not be started (e.g. OAuth not set up).
# The agent then runs without email and the system prompt says so.
EMAIL_AVAILABLE = True


def _load_gmail_tools_or_none():
    """Gmail is optional: if the MCP server fails to start, run without email.

    Of all the Gmail MCP tools (read, search, delete, labels, filters, ...) the agent gets ONE:
    a send_email wrapper whose recipient is fixed in code to the logged-in customer.
    """
    global EMAIL_AVAILABLE
    try:
        mcp_tools = asyncio.run(load_gmail_tools())
    except Exception as error:  # MCP start-up errors arrive as an ExceptionGroup
        EMAIL_AVAILABLE = False
        print(f"WARNING: Gmail MCP unavailable, running without email ({type(error).__name__}: {error})")
        return []

    mcp_send = next((tool for tool in mcp_tools if tool.name == "send_email"), None)
    if mcp_send is None:
        EMAIL_AVAILABLE = False
        print("WARNING: Gmail MCP has no send_email tool, running without email")
        return []
    EMAIL_AVAILABLE = True
    return [_customer_send_email_tool(mcp_send)]


def _call_mcp_tool(tool, **kwargs):
    """Call an MCP tool synchronously and return its text content.

    MCPAdapter tools are async-only, so call tool.coroutine directly (ainvoke with
    response_format="content_and_artifact" drops the content the first time)."""
    if getattr(tool, "coroutine", None) is not None:
        result = asyncio.run(tool.coroutine(**kwargs))
    else:
        result = tool.func(**kwargs)
    if isinstance(result, tuple):  # (content, artifact)
        result = result[0]
    return result if isinstance(result, str) else str(result)


def _customer_send_email_tool(mcp_send):
    """send_email(subject, body): the recipient always comes from runtime.context, never from the model."""

    @tool
    def send_email(subject: str, body: str, runtime: ToolRuntime[SessionContext]) -> str:
        """Send an email to the logged-in customer (the recipient is set automatically).
        subject and body must say what changed (order or return, and the new status)."""
        recipient = runtime.context.user_email
        return _call_mcp_tool(mcp_send, to=[recipient], subject=subject, body=body)

    return send_email


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a customer support assistant for an e-commerce store.
The logged-in customer's email is: {user_email}

CAPABILITY 1 - Policy questions (search_policies)
Use search_policies for returns policy, return window, refunds and refund timelines, shipping,
cancellation policy and return shipping costs. Answer from the tool results.
Do not use any tool for simple greetings.

CAPABILITY 2 - Customer data (customer data tools)
These tools always return data for the logged-in customer only. They take no email argument.
- get_my_orders(status, limit): the customer's orders, newest first (optional status filter)
- get_order_details(order_id): one order's items (exact product names), payments and returns
- get_my_returns(): the customer's return requests and their status
- get_my_tickets(): the customer's support tickets
- get_my_spend(): order count and total spent, excluding cancelled orders
If the customer asks about another person's account or orders, refuse: you can only help with their own.
If a tool says an order was not found, tell the customer it is not on their account.

CAPABILITY 3 - Email (send_email)
send_email(subject, body) always goes to the logged-in customer.
The subject and body must say what changed (order or return, and the new status).
After each successful send_email, call log_email(subject, body_preview, email_type) once.
If send_email fails, do not call log_email and tell the customer the email could not be sent.

CAPABILITY 4 - Cancel or return (cancel_order_action, create_return_action)
Follow these steps, one tool per turn:
1. Call get_order_details(order_id) to verify the order belongs to the customer and check its status.
   For a return, use the exact product name from its items and ask for a reason if not given.
2. Only PLACED orders can be cancelled. Only SHIPPED or DELIVERED orders can be returned.
   If the order is PLACED and the customer asks for a return, refuse and suggest cancelling instead.
   A DELIVERED order can only be returned within 30 days of its delivered_at date; if it is older,
   refuse and quote the returns policy.
3. Send a short "request received" email with send_email, then call log_email
   (email_type 'CANCEL_REQUEST' or 'RETURN_REQUEST').
4. Call cancel_order_action or create_return_action once.
   These need admin approval. Never say the request is approved - tell the customer a human will review it.

AFTER THE ADMIN DECIDES
- If the action tool result says the order is cancelled or the return is approved: send_email with the
  result (email_type 'CANCEL_APPROVED' or 'RETURN_APPROVED'), call log_email, then confirm to the customer.
- If the admin rejected the request: send_email saying it was rejected (email_type 'CANCEL_REJECTED' or
  'RETURN_REJECTED'), call log_email, then tell the customer.
"""


NO_EMAIL_NOTE = """
EMAIL IS NOT AVAILABLE in this deployment: there is no send_email tool. Skip every send_email and
log_email step above and do not say an email was sent. Tell the customer the outcome in the chat instead.
"""


@dynamic_prompt
def support_prompt(request):
    """Fill the logged-in user's email into the system prompt."""
    prompt = SYSTEM_PROMPT.format(user_email=request.runtime.context.user_email)
    if not EMAIL_AVAILABLE:
        prompt += NO_EMAIL_NOTE
    return prompt


# ---------------------------------------------------------------------------
# Checkpointer + human-in-the-loop
# ---------------------------------------------------------------------------

_checkpointer = None


def get_checkpointer():
    global _checkpointer
    if _checkpointer is None:
        path = os.getenv("CHECKPOINTS_DB_PATH", "checkpoints.sqlite")
        conn = sqlite3.connect(path, check_same_thread=False)
        _checkpointer = SqliteSaver(conn=conn)
        _checkpointer.setup()
    return _checkpointer


def get_thread_config(user_email, conversation_id):
    return {
        "configurable": {"thread_id": f"{user_email}:{conversation_id}"},
        # With the HITL middleware each tool round takes ~3 graph steps, so the
        # default of 20 can stop a normal cancel/return flow. 50 leaves enough room.
        "recursion_limit": 50,
    }


def get_hitl_middleware():
    return HumanInTheLoopMiddleware(
        interrupt_on={
            "cancel_order_action": {"allowed_decisions": ["approve", "edit", "reject"]},
            "create_return_action": {"allowed_decisions": ["approve", "edit", "reject"]},
        }
    )


# ---------------------------------------------------------------------------
# The agent (one instance per process)
# ---------------------------------------------------------------------------

def create_support_agent():
    email_tools = _load_gmail_tools_or_none()
    print("Email tools:", [t.name for t in email_tools])

    model = get_chat_model()  # OpenAI, Groq, Gemini or Ollama: see llm.py / .env
    print("Model:", describe_provider())

    tools = [search_policies] + get_customer_tools() + email_tools + get_action_tools()
    # The retry middleware wraps the logging middleware, so every attempt (and each 429) is logged
    middleware = [support_prompt, get_hitl_middleware(), get_retry_middleware()] + get_logging_middleware()

    return create_agent(
        model=model,
        tools=tools,
        middleware=middleware,
        context_schema=SessionContext,
        checkpointer=get_checkpointer(),
    )


_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = create_support_agent()
    return _agent


class NoPendingRequestError(RuntimeError):
    """The thread has no paused cancel/return request to decide (already decided, or never paused)."""


def resume_with_decision(thread_id, decision, edited_args=None, message="", admin_email=None):
    """Resume a paused customer thread with the admin's decision.

    decision: "approve", "edit" or "reject"
    edited_args: new tool arguments when decision is "edit"
    admin_email: the deciding admin; passed to the action tools through SessionContext
    Returns the agent result. Raises NoPendingRequestError if the thread is not paused.
    """
    user_email, conversation_id = thread_id.rsplit(":", 1)
    config = get_thread_config(user_email, conversation_id)
    agent = get_agent()

    interrupts = agent.get_state(config).interrupts
    if not interrupts:
        raise NoPendingRequestError(
            f"Thread {thread_id} has no request waiting for a decision. It may already have been decided."
        )

    decisions = []
    for interrupt in interrupts:
        for request in interrupt.value.get("action_requests", []):
            if decision == "approve":
                decisions.append({"type": "approve"})
            elif decision == "edit":
                new_args = {**request["args"], **(edited_args or {})}
                decisions.append({"type": "edit", "edited_action": {"name": request["name"], "args": new_args}})
            else:
                text = "The admin REJECTED this request. Nothing was changed."
                if message:
                    text += f" Reason: {message}"
                decisions.append({"type": "reject", "message": text})

    return agent.invoke(
        Command(resume={"decisions": decisions}),
        config=config,
        context=SessionContext(user_email=user_email, conversation_id=conversation_id, role="admin",
                               admin_email=admin_email),
    )
