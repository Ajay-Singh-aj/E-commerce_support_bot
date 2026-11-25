# Agensi listing - copy to paste

**Skill name:** E-commerce Support Desk

**Upload:** `dist/ecommerce-support-desk.zip` (build with `python tools/build_skill.py`)

**Price:** Free (first listing; switch to paid later if it gets traction)

**Agensi tools to select:** none. The skill runs fully offline on the buyer's machine.

**Category / tags:** Customer support, E-commerce, Operations, SQLite, Human-in-the-loop

**Short description (one line):**
Turn your agent into an e-commerce support desk: order lookups, refunds, returns and policy answers, with cancellations and returns only after your approval.

**Full description:**
E-commerce Support Desk gives Claude Code, Codex, Cursor and other SKILL.md agents everything they need to
work a store's support queue:

- Look up a customer's orders, line items, payments, returns, tickets and total spend
- Answer returns, refunds, shipping and cancellation questions from your own policy files, citing the source
- Cancel PLACED orders and create returns for SHIPPED/DELIVERED items, always as a dry run first, applied only after you say yes
- Draft the reply email to the customer

What you need: Python 3.9+. No API keys, no accounts, no network calls.

What's included: a fictional demo store (Voltcart: 3 customers, 20 products, 20 orders, INR) so it works
in 30 seconds, plus a schema reference for connecting your own SQLite database and policy folder.

Limits: SQLite only (export other databases to SQLite). Approving a return records a pending refund
(issued after the item is received and inspected); it does not move money through a payment provider.
Demo data is fictional.

**Example prompts:**
- "Set up the support desk demo."
- "priya.sharma@example.com asks where her latest order is."
- "What is the return window and who pays for the pickup?"
- "rahul.verma@example.com wants to cancel order 14."
