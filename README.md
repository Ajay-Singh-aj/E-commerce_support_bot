# ShopDesk AI - Agentic Customer Support for E-commerce

ShopDesk AI is a customer-support assistant for online stores. It answers order, payment, return and
policy questions in natural language, and it handles cancellations and returns end to end, with a human
approving every change before it touches the database.

It ships in two forms:

| | **ShopDesk Web App** | **Support Desk Agent Skill** |
|---|---|---|
| For | Stores that want a branded customer chat + admin console | Operators who already use Claude Code, Codex, Cursor or another SKILL.md agent |
| Stack | Streamlit, LangChain 1.x, LangGraph, ChromaDB, SQLite, Gmail MCP | Python standard library only |
| Needs | ONE model key: OpenAI (paid) or Groq / Gemini (free), or a local Ollama; Gmail OAuth optional | Nothing: no API keys, no network calls |
| Folder | `app.py`, `app_admin.py`, `src/` | `skills/ecommerce-support-desk/` |

Both run against **Voltcart**, a fictional demo electronics store (3 customers, 20 products, 20 orders,
prices in INR). All demo data is invented. Demo dates are moved to today each time the demo database is
built, so it always looks recent (set `SHOPDESK_NO_DATE_SHIFT=1` to keep the fixed dates). Swap in your
own database and policy documents to go live.

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
Customer (Streamlit :8501) ──► LangChain 1.x agent (OpenAI / Groq / Gemini / Ollama, see llm.py)
                                 ├─ search_policies ─► ChromaDB ◄─ policies/*.txt (OpenAI / Gemini / local embeddings)
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

Create `.env` from the example and put **ANY ONE** key in it (`GROQ_API_KEY` is free and quick to get),
or run Ollama locally. See [Choose your model](#choose-your-model-free-or-paid) below.

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

Both pages show the model in use (for example `Model: groq / llama-3.3-70b-versatile`).

## Choose your model (free or paid)

ShopDesk runs on whichever key you have. With `LLM_PROVIDER=auto` (the default) it uses the first one
available, in this order: OpenAI → Groq → Gemini → Ollama. Set `LLM_PROVIDER` to pick one explicitly,
and `LLM_MODEL` to override the model.

| Provider | `.env` | Default model | Get a key | Cost / free tier |
|---|---|---|---|---|
| **OpenAI** | `OPENAI_API_KEY` | `gpt-4o-mini` | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | Paid, per token |
| **Groq** | `GROQ_API_KEY` | `openai/gpt-oss-120b` | [console.groq.com/keys](https://console.groq.com/keys) | Free tier with per-minute and per-day request/token limits; see [your limits](https://console.groq.com/settings/limits) |
| **Gemini** | `GOOGLE_API_KEY` | `gemini-flash-latest` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | Free tier with per-minute and per-day limits; see [your limits](https://aistudio.google.com/rate-limit). Paid tier available |
| **Ollama** | `OLLAMA_BASE_URL` (no key) | `qwen2.5:7b` | [ollama.com/download](https://ollama.com/download), then `ollama pull qwen2.5:7b` | Free, runs on your machine (8 GB+ RAM recommended) |

Free-tier limits change often, so check the provider's limits page. ShopDesk retries rate-limited
(HTTP 429) model calls with exponential backoff, but a busy free tier can still be slow (on Groq's free
tier a cancel or return conversation took about 15-20 s in our live eval because of 429 waits).
Which models a key can use also depends on the plan: Groq's free plan offers `openai/gpt-oss-120b`,
`openai/gpt-oss-20b` and `qwen/qwen3.8-27b`, but not `llama-3.3-70b-versatile`. If you get
"model does not exist or you do not have access to it", set `LLM_MODEL` to a model your key lists.

To check answer quality on your provider, run the live eval (10 real conversations, about 25 model
calls; results go to `evals/`):

```
uv run python tests/live_eval.py
```
`gemini-flash-latest` is Google's alias for its newest Flash model; if your account rejects it, set
`LLM_MODEL` to a current Flash model from [Google's model list](https://ai.google.dev/gemini-api/docs/models).

> **Data privacy:** on the **free (unpaid) Gemini tier, Google may use your prompts and responses to
> improve its products, and human reviewers may read them** ([Gemini API terms](https://ai.google.dev/gemini-api/terms)).
> For real customer data use OpenAI, the paid Gemini tier, or Ollama (data stays on your machine).
> Check Groq's data policy for your plan before sending real customer data.

**Policy-search embeddings** (`EMBEDDINGS_PROVIDER`): `auto` uses OpenAI when `OPENAI_API_KEY` is set,
otherwise **local** FastEmbed (`BAAI/bge-small-en-v1.5`, no key, downloads about 70 MB once). Gemini
embeddings are used only if you set `EMBEDDINGS_PROVIDER=gemini`, because of the free-tier data terms.
When the embeddings change, the index is rebuilt automatically and the old one is kept as `chroma_db.old-<timestamp>/`.

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
    llm.py                 Model + embeddings provider selection (OpenAI, Groq, Gemini, Ollama, local)
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
  A DELIVERED order can be returned within **30 days** of `orders.delivered_at`.
- An approved return records the refund as **REFUND_PENDING**: the refund is issued after the item is
  received and inspected.
- Return and cancellation requests wait in `pending_actions` until an admin decides. The admin console
  claims each request atomically (`PROCESSING`), so a second admin or a stale page cannot decide it twice,
  and records `decided_by` / `decided_at`. Approved returns also store the admin in `returns.admin_id`.
- The customer's email tool can only send to the logged-in customer. No other Gmail tools are exposed.
- Logins lock for 15 minutes after 5 failed attempts; a chat session is capped at 30 messages.

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
