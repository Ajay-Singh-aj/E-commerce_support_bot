"""Logging middleware.

Logs every model call and tool call (SQL, RAG, Gmail MCP, action tools) to the
terminal and to logs/app.log. Each line: timestamp | user_email | action | input | result.

Both hooks are normal `def` functions (not async) because the app uses agent.invoke().
"""

import logging
from datetime import datetime
from pathlib import Path

from langchain.agents.middleware import wrap_model_call, wrap_tool_call
from langchain_core.messages import ToolMessage

LOG_FILE = Path(__file__).resolve().parents[2] / "logs" / "app.log"
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("support_agent")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.FileHandler(LOG_FILE, encoding="utf-8"))
    logger.addHandler(logging.StreamHandler())


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _preview(text, length=120):
    text = str(text).replace("\n", " ")
    return text if len(text) <= length else text[:length] + "..."


def _user_email(request):
    context = getattr(request.runtime, "context", None)
    return getattr(context, "user_email", "-")


@wrap_model_call
def model_logging_middleware(request, handler):
    user = _user_email(request)
    last = request.messages[-1].content if request.messages else ""
    logger.info(f"{_ts()} | {user} | MODEL REQUEST | {len(request.messages)} messages | last: {_preview(last)}")

    try:
        response = handler(request)
    except Exception as error:
        logger.error(f"{_ts()} | {user} | MODEL ERROR | {error}")
        raise

    message = response.result[-1]
    if message.tool_calls:
        names = [call["name"] for call in message.tool_calls]
        logger.info(f"{_ts()} | {user} | MODEL RESPONSE | tool_calls: {names}")
    else:
        logger.info(f"{_ts()} | {user} | MODEL RESPONSE | {_preview(message.content)}")

    return response


@wrap_tool_call
def tool_logging_middleware(request, handler):
    user = _user_email(request)
    name = request.tool_call["name"]
    args = request.tool_call.get("args", {})
    logger.info(f"{_ts()} | {user} | TOOL {name} | input: {_preview(args)}")

    try:
        result = handler(request)
    except Exception as error:
        # Record the error and give it back to the agent so it can tell the user
        logger.error(f"{_ts()} | {user} | TOOL {name} | FAILED | {error}")
        return ToolMessage(
            content=f"Error: {name} failed: {error}",
            tool_call_id=request.tool_call["id"],
            name=name,
            status="error",
        )

    content = getattr(result, "content", result)
    if getattr(result, "status", "success") == "error":
        logger.error(f"{_ts()} | {user} | TOOL {name} | FAILED | {_preview(content)}")
    else:
        logger.info(f"{_ts()} | {user} | TOOL {name} | SUCCESS | {_preview(content)}")
    return result


def get_logging_middleware():
    return [model_logging_middleware, tool_logging_middleware]
