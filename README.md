# ShopDesk AI - Agentic Customer Support for E-commerce

ShopDesk AI is a customer-support assistant for online stores. It answers order, payment, return and
policy questions in natural language, and it handles cancellations and returns end to end, with a human
approving every change before it touches the database.

It ships in two forms:

| | **ShopDesk Web App** | **Support Desk Agent Skill** |
|---|---|---|
| For | Stores that want a branded customer chat + admin console | Operators who already use Claude Code, Codex, Cursor or another SKILL.md agent |
| Stack | Streamlit, LangChain 1.x, LangGraph, ChromaDB, SQLite, Gmail MCP | Python standard library only |
| Needs | OpenAI API key; Gmail OAuth optional (for email notifications) | Nothing: no API keys, no network calls |
| Folder | `app.py`, `app_admin.py`, `src/` | `skills/ecommerce-support-desk/` |

Both run against **Voltcart**, a fictional demo electronics store (3 customers, 20 products, 20 orders,
prices in INR). All demo data is invented. Swap in your own database and policy documents to go live.

## Features

- **Order intelligence:** order status, line items, payments, refunds, spend summaries, from SQLite
- **Policy answers grounded in your documents:** returns, refunds, shipping, cancellations (RAG over ChromaDB in the app, BM25 in the skill)
- **Self-service actions with human-in-the-loop:** cancel PLACED orders, return SHIPPED/DELIVERED items; every request pauses for admin approval (approve, edit or reject)
- **Admin console:** queue of pending requests, ticket view, one-click decisions that resume the customer's conversation
- **Customer notifications:** request-received and decision emails through the Gmail MCP server, logged in `email_logs`
- **Data isolation:** every data tool is scoped in code to the logged-in customer (no free-form SQL); action tools re-check order ownership
- **Persistent conversations:** LangGraph SQLite checkpointer, multiple chats per customer
- **Audit logging:** every model and tool call logged to `logs/app.log`
- **Hashed credentials:** PBKDF2-SHA256 password hashing

## Architecture (web app)

```
Customer (Streamlit :8501) ──► LangChain 1.x agent (gpt-4o-mini)
                                 ├─ search_policies ─► ChromaDB ◄─ policies/*.txt
                                 ├─ customer data tools ► SQLite (ecommerce.db, read-only)
                                 ├─ Gmail MCP ───────► send_email
                                 └─ cancel / return ─► HumanInTheLoopMiddleware ─► pending_actions
Admin (Streamlit :8502) ──► approve / edit / reject ─► Command(resume=…) on the same thread
State: LangGraph SqliteSaver (checkpoints.sqlite), thread id = user_email:conversation_id
```

## Quick start - web app

Requires [uv](https://docs.astral.sh/uv/) (it installs Python 3.11+ for you). Node.js is only needed for
the optional Gmail MCP server.

```
uv sync
uv run python -m langchain_bot.db_init
```

Create `.env` from the example and put your `OPENAI_API_KEY` in it:

```
copy .env.example .env        (Windows)
cp .env.example .env          (macOS / Linux)
```

Optional, for email notifications: put `gcp-oauth.keys.json` in `~/.gmail-mcp/`
(`%USERPROFILE%\.gmail-mcp\` on Windows) and authorise once. Without it the app runs and skips emails.

```
npx -y @gongrzhe/server-gmail-autoauth-mcp auth
```

Start the two apps in separate terminals:

```
uv run streamlit run app.py
uv run streamlit run app_admin.py --server.port 8502
```

Customer chat: http://localhost:8501 · Admin console: http://localhost:8502

Demo accounts (fictional):

| Role | Email | Password |
|---|---|---|
| Customer | priya.sharma@example.com | Priya@123 |
| Customer | rahul.verma@example.com | Rahul@123 |
| Customer | ananya.iyer@example.com | Ananya@123 |
| Admin | admin@example.com | Admin@123 |

## Quick start - agent skill

Build the zip (writes `dist/ecommerce-support-desk.zip`):

```
uv run python tools/build_skill.py
```

Unzip into your agent's skills folder (for Claude Code: `~/.claude/skills/ecommerce-support-desk/`) and ask
"Set up the support desk demo". See [`skills/ecommerce-support-desk/README.md`](skills/ecommerce-support-desk/README.md).

## Going live with your own store

1. Replace `ecommerce_setup.sql` seed data with your catalogue, or point the app at your own SQLite database with the same schema.
2. Replace the files in `policies/` with your store's policies, then delete `chroma_db/` so the index is rebuilt.
3. Optionally add your store's name and tone of voice to the system prompt in `src/langchain_bot/agent.py`.
4. Rotate the demo passwords (`langchain_bot.auth.hash_password`) and restrict who can open the admin console.

## Project structure

```
app.py                     Customer chat: login, multiple conversations, agent chat
app_admin.py               Admin console: pending requests (approve / edit / reject), tickets
ecommerce_setup.sql        Schema + Voltcart demo data
policies/                  Returns, shipping and FAQ documents (demo store)
src/langchain_bot/
    agent.py               Agent, Gmail MCP tools, checkpointer, HITL, resume_with_decision
    action_tools.py        cancel_order_action, create_return_action
    rag_tool.py            search_policies (ChromaDB)
    customer_tools.py      Customer-scoped data tools (orders, returns, tickets, spend, log_email)
    hitl_utils.py          Pending-action persistence
    auth.py                PBKDF2 login
    middleware.py          Model / tool call logging
    context.py, db_init.py
skills/ecommerce-support-desk/   SKILL.md agent skill (stdlib scripts, no keys)
tools/build_skill.py       Packages the skill as a marketplace-ready zip
```

## Business rules

- Only **PLACED** orders can be cancelled; the payment is marked REFUNDED.
- Only **SHIPPED** or **DELIVERED** orders can be returned, one product per request, with a reason.
- Return and cancellation requests wait in `pending_actions` until an admin decides.

## Roadmap

- PostgreSQL / MySQL adapters and Shopify / WooCommerce connectors
- WhatsApp and website chat widget channels
- Multi-language replies (Hindi and regional languages)
- Analytics: deflection rate, top intents, time to resolution

## Implementation notes

- `recursion_limit` is 50: with the HITL middleware each tool round takes about 3 graph steps.
- The Gmail MCP wrapper calls `tool.coroutine(**kwargs)`: with `response_format="content_and_artifact"`,
  `ainvoke` returns only the content and the first `send_email` fails.

## License and contact

MIT - see [LICENSE](LICENSE). Built by **Ajay Singh** ([ajay10022004@gmail.com](mailto:ajay10022004@gmail.com)).
Want ShopDesk AI set up on your store's data? Get in touch.
