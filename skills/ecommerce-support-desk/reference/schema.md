# Database schema used by the scripts

To run the skill against your own store, provide a SQLite file with at least these tables and columns.
Extra tables and columns are ignored. The full DDL is in `data/demo_store.sql`.

| Table | Columns the scripts use | Notes |
|---|---|---|
| `users` | `id`, `email`, `full_name`, `role`, `created_at` | `role` = `customer` or `admin`. Emails are matched lower-case. |
| `products` | `id`, `name` | Return requests match `name` case-insensitively. |
| `orders` | `id`, `user_id`, `status`, `order_date`, `total_amount` | `status` = `PLACED`, `SHIPPED`, `DELIVERED` or `CANCELLED`. |
| `order_items` | `id`, `order_id`, `product_id`, `quantity`, `unit_price` | |
| `payments` | `order_id`, `amount`, `status`, `payment_method`, `transaction_reference`, `paid_at` | `status` = `PAID` or `REFUNDED`. |
| `returns` | `id`, `order_id`, `order_item_id`, `user_id`, `reason`, `status`, `requested_at`, `resolved_at` | `status` = `PENDING`, `APPROVED` or `REJECTED`. |
| `tickets` | `id`, `user_id`, `return_id`, `subject`, `status`, `thread_id`, `user_email`, `created_at`, `updated_at` | `status` = `OPEN`, `IN_REVIEW`, `RESOLVED` or `CLOSED`. |

Writes happen only in `scripts/actions.py --confirm`:
- cancel: `UPDATE orders`, `UPDATE payments`, `INSERT INTO tickets`
- return: `INSERT INTO returns`, `INSERT INTO tickets`, `INSERT INTO payments` (refund row)

Back up your database before using `--confirm` on real data.
