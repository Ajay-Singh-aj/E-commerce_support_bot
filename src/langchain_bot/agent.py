"""The ShopDesk support agent.

Tools: search_policies (RAG) + SQL toolkit + Gmail MCP tools + cancel/return action tools.
Also: SQLite checkpointer, human-in-the-loop middleware, logging middleware.
"""

import asyncio
import os
import sqlite3

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware, dynamic_prompt
from langchain.mcp import MCPAdapter
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from langchain_bot.action_tools import get_action_tools
from langchain_bot.context import SessionContext
from langchain_bot.middleware import get_logging_middleware
from langchain_bot.rag_tool import search_policies
from langchain_bot.sql_tools import get_sql_tools

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


def _sync_mcp_tool(tool):
    """MCPAdapter tools are async-only. agent.invoke needs a sync func."""
    if getattr(tool, "func", None) is not None:
        return tool

    def _call(**kwargs):
        # tool.coroutine returns the (content, artifact) pair this tool type expects
        return asyncio.run(tool.coroutine(**kwargs))

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        func=_call,
        response_format=getattr(tool, "response_format", "content_and_artifact"),
    )


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a customer support assistant for an e-commerce store.
The logged-in customer's email is: {user_email}

CAPABILITY 1 - Policy questions (search_policies)
Use search_policies for returns policy, return window, refunds and refund timelines, shipping,
cancellation policy and return shipping costs. Answer from the tool results.
Do not use any tool for simple greetings.

CAPABILITY 2 - Customer data (SQL tools)
Use the SQL tools for the customer's orders, order items, payments, returns, tickets and spending.
SECURITY RULE: always filter by the logged-in customer's email. Never return other users' rows.
Examples:
  SELECT o.id, o.status, o.order_date, o.total_amount
  FROM orders o JOIN users u ON o.user_id = u.id
  WHERE u.email = '{user_email}' ORDER BY o.order_date DESC LIMIT 5;

  SELECT t.id, t.subject, t.status, t.created_at
  FROM tickets t WHERE t.user_email = '{user_email}';
Never select the password column. Only use SELECT, except the INSERT into email_logs described below.
Use at most 3 SQL query attempts per question.

CAPABILITY 3 - Email (send_email)
Use only send_email. The "to" list must contain only {user_email}.
The subject and body must say what changed (order or return, and the new status).
After each successful send_email, use the SQL tool to insert a row into email_logs:
  INSERT INTO email_logs (user_id, email, subject, body_preview, email_type, sent_at)
  VALUES ((SELECT id FROM users WHERE email = '{user_email}'), '{user_email}',
          '<subject>', '<first 100 characters of body>', '<EMAIL_TYPE>', datetime('now'));
If send_email fails, do not insert into email_logs and tell the customer the email could not be sent.
Never call delete_email, batch_delete_emails, or any label or filter tools.

CAPABILITY 4 - Cancel or return (cancel_order_action, create_return_action)
Follow these steps, one tool per turn:
1. Use SQL to verify the order belongs to the customer and check its status.
   For a return, get the exact product name from the order items and ask for a reason if not given.
2. Only PLACED orders can be cancelled. Only SHIPPED or DELIVERED orders can be returned.
   If the order is PLACED and the customer asks for a return, refuse and suggest cancelling instead.
3. Send a short "request received" email with send_email, then insert the email_logs row
   (email_type 'CANCEL_REQUEST' or 'RETURN_REQUEST').
4. Call cancel_order_action or create_return_action once.
   These need admin approval. Never say the request is approved - tell the customer a human will review it.

AFTER THE ADMIN DECIDES
- If the action tool result says the order is cancelled or the return is approved: send_email with the
  result (email_type 'CANCEL_APPROVED' or 'RETURN_APPROVED'), insert the email_logs row, then confirm to the customer.
- If the admin rejected the request: send_email saying it was rejected (email_type 'CANCEL_REJECTED' or
  'RETURN_REJECTED'), insert the email_logs row, then tell the customer.
"""


@dynamic_prompt
def support_prompt(request):
    """Fill the logged-in user's email into the system prompt."""
    return SYSTEM_PROMPT.format(user_email=request.runtime.context.user_email)


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
    gmail_tools = [_sync_mcp_tool(tool) for tool in asyncio.run(load_gmail_tools())]
    print("Gmail MCP tools:", [tool.name for tool in gmail_tools])

    tools = [search_policies] + get_sql_tools() + gmail_tools + get_action_tools()
    middleware = [support_prompt, get_hitl_middleware()] + get_logging_middleware()

    return create_agent(
        model=ChatOpenAI(model="gpt-4o-mini", temperature=0.2),
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


def resume_with_decision(thread_id, decision, edited_args=None, message=""):
    """Resume a paused customer thread with the admin's decision.

    decision: "approve", "edit" or "reject"
    edited_args: new tool arguments when decision is "edit"
    Returns the agent result.
    """
    user_email, conversation_id = thread_id.rsplit(":", 1)
    config = get_thread_config(user_email, conversation_id)
    agent = get_agent()

    decisions = []
    for interrupt in agent.get_state(config).interrupts:
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
        context=SessionContext(user_email=user_email, conversation_id=conversation_id, role="admin"),
    )
