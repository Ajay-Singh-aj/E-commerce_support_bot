# E-commerce Support Desk - agent skill

Turns Claude Code, Codex, Cursor or any SKILL.md-compatible agent into a customer-support desk for an
online store: order status, refunds, returns, tickets and policy answers, with cancellations and returns
gated behind your explicit approval.

- No API keys, no network calls, Python standard library only
- Works out of the box with a fictional demo store (Voltcart, 3 customers, 20 orders)
- Point it at your own SQLite database and policy files when you are ready

## Install

Unzip into your agent's skills folder, for example:

```
~/.claude/skills/ecommerce-support-desk/      # Claude Code (personal)
<project>/.claude/skills/ecommerce-support-desk/   # Claude Code (project)
```

Requires Python 3.9 or newer on PATH.

## Try it

Ask your agent:

- "Set up the support desk demo."
- "priya.sharma@example.com asks where her latest order is."
- "What is the return window and who pays for the pickup?"
- "rahul.verma@example.com wants to cancel order 14."   (the agent shows a dry run and waits for your yes)

## Use your own store

- Database: `SHOPDESK_DB=/path/store.db` or `--db` (schema: `reference/schema.md`)
- Policies: `SHOPDESK_POLICIES=/path/policies` or `--dir` (any `.md` / `.txt` files)

## What it changes

Only `scripts/actions.py --confirm` writes to the database, and the skill instructs the agent to run it
only after you approve the dry-run summary. Back up real data before use.

Part of ShopDesk AI by Ajay Singh - https://github.com/Ajay-Singh-aj/E-commerce_support_bot
License: MIT (see LICENSE).
