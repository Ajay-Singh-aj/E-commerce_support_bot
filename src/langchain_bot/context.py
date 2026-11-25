"""Who is using the agent right now.

Passed as `context` on every agent.invoke(). Tools read it through
runtime.context, so the model only passes order_id, product_name, etc.
"""

from dataclasses import dataclass


@dataclass
class SessionContext:
    user_email: str
    conversation_id: str
    role: str = "customer"
