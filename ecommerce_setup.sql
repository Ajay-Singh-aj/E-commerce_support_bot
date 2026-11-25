-- ShopDesk AI - demo database for "Voltcart", a fictional online electronics store.
-- SQLite. All people, orders and payments below are invented sample data.
-- Dates are relative to 2026-11-25 and are shifted to today when the database is built
-- (db_init / setup_demo.py; disable with SHOPDESK_NO_DATE_SHIFT=1 or --no-shift). Amounts are in INR.

DROP TABLE IF EXISTS email_logs;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS conversations;
DROP TABLE IF EXISTS pending_actions;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS returns;
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS users;

-- Accounts. role = customer | admin. password holds a PBKDF2-SHA256 hash.
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  full_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('customer', 'admin')),
  created_at TEXT NOT NULL
);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  price REAL NOT NULL,
  stock_qty INTEGER NOT NULL
);

-- status = PLACED | SHIPPED | DELIVERED | CANCELLED
-- delivered_at is set for DELIVERED orders; the 30-day return window counts from it.
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  order_date TEXT NOT NULL,
  total_amount REAL NOT NULL,
  delivered_at TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  unit_price REAL NOT NULL,
  FOREIGN KEY (order_id) REFERENCES orders(id),
  FOREIGN KEY (product_id) REFERENCES products(id)
);

-- status = PAID | REFUNDED | REFUND_PENDING.
-- Cancelling a PLACED order marks its payment REFUNDED. An approved return adds an extra
-- REFUND_PENDING row for the item amount; the refund is issued after the item is received and inspected.
CREATE TABLE payments (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL,
  amount REAL NOT NULL,
  status TEXT NOT NULL,
  payment_method TEXT NOT NULL,
  transaction_reference TEXT,
  paid_at TEXT,
  FOREIGN KEY (order_id) REFERENCES orders(id)
);

CREATE TABLE returns (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL,
  order_item_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
  requested_at TEXT NOT NULL,
  resolved_at TEXT,
  admin_id INTEGER,
  FOREIGN KEY (order_id) REFERENCES orders(id),
  FOREIGN KEY (order_item_id) REFERENCES order_items(id),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (admin_id) REFERENCES users(id)
);

-- Support tickets. thread_id links a ticket to the chat thread that raised it.
CREATE TABLE tickets (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  return_id INTEGER,
  subject TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('OPEN', 'IN_REVIEW', 'RESOLVED', 'CLOSED')),
  thread_id TEXT,
  user_email TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (return_id) REFERENCES returns(id)
);

-- Cancel / return requests paused for human approval in the admin dashboard.
-- PROCESSING = claimed by an admin while the customer's thread is resumed.
-- decided_by / decided_at = which admin decided, and when.
CREATE TABLE pending_actions (
  id INTEGER PRIMARY KEY,
  thread_id TEXT NOT NULL,
  user_email TEXT NOT NULL,
  action_type TEXT NOT NULL CHECK (action_type IN ('CANCEL_ORDER', 'CREATE_RETURN')),
  order_id INTEGER NOT NULL,
  product_name TEXT,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'PROCESSING', 'APPROVED', 'REJECTED')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT,
  FOREIGN KEY (order_id) REFERENCES orders(id)
);

-- Optional reporting tables (the live chat history is kept by the LangGraph checkpointer).
CREATE TABLE conversations (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  role TEXT NOT NULL,
  started_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'CLOSED')),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE messages (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL,
  sender_type TEXT NOT NULL CHECK (sender_type IN ('user', 'bot', 'admin')),
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE email_logs (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body_preview TEXT,
  email_type TEXT NOT NULL,
  sent_at TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- ---------------------------------------------------------------------------
-- Demo data
-- ---------------------------------------------------------------------------
-- Users: 3 demo customers + 1 admin. Passwords are PBKDF2-SHA256 hashes;
-- the plain demo passwords are listed in README.md.

INSERT INTO users (id, email, password, full_name, role, created_at) VALUES
  (1, 'priya.sharma@example.com', 'pbkdf2_sha256$200000$voltcart-demo-1$d3784456de7ed325b934d9eca21f23d24872406e58c661b59b5439c8e7af955b', 'Priya Sharma', 'customer', '2026-09-02T10:00:00'),
  (2, 'rahul.verma@example.com', 'pbkdf2_sha256$200000$voltcart-demo-2$e8f2fa0a898818a1143d10a63c46e74ae79c6e7fe7fb1ac0f497352d5c9f006d', 'Rahul Verma', 'customer', '2026-09-14T11:30:00'),
  (3, 'ananya.iyer@example.com', 'pbkdf2_sha256$200000$voltcart-demo-3$917b8716e77748e5fc406b1ee584c9da47dc76da4ccba8d4beecb0b5d696994a', 'Ananya Iyer', 'customer', '2026-10-01T09:15:00'),
  (4, 'admin@example.com', 'pbkdf2_sha256$200000$voltcart-demo-4$7d7eccae770c23cbf4af84714e5f72448862817d0ce4f22b17d7eadd9e67c8d9', 'Store Admin', 'admin', '2026-08-28T09:00:00');

-- Product catalogue (prices in INR)

INSERT INTO products (id, name, description, price, stock_qty) VALUES
  (1, 'Voltcart AirBuds Pro', 'True-wireless earbuds with ANC, 30 h battery', 3499.00, 140),
  (2, 'Voltcart PowerCube 20000', '20,000 mAh power bank, 22.5 W fast charge', 1899.00, 210),
  (3, 'Voltcart GaN Charger 65W', 'Dual USB-C GaN wall charger', 2299.00, 160),
  (4, 'Voltcart Braided USB-C Cable 1.5m', '100 W braided charging cable', 399.00, 500),
  (5, 'Voltcart SmartBand 3', 'Fitness band with SpO2 and sleep tracking', 2799.00, 95),
  (6, 'Voltcart Mechanical Keyboard TKL', 'Hot-swap tenkeyless keyboard, brown switches', 4599.00, 60),
  (7, 'Voltcart Silent Mouse', 'Wireless silent-click mouse, 2.4 GHz + Bluetooth', 899.00, 230),
  (8, 'Voltcart 27in Monitor Arm', 'Gas-spring single monitor arm', 2499.00, 70),
  (9, 'Voltcart Laptop Backpack 15.6', 'Water-resistant backpack with USB port', 1999.00, 120),
  (10, 'Voltcart HD Webcam 1080p', 'Full-HD webcam with dual mics', 2199.00, 85),
  (11, 'Voltcart Smart Plug 16A', 'Wi-Fi smart plug with energy monitoring', 1099.00, 300),
  (12, 'Voltcart LED Desk Lamp', 'Dimmable lamp with wireless charging base', 1799.00, 110),
  (13, 'Voltcart Phone Tripod', 'Extendable tripod with Bluetooth remote', 799.00, 260),
  (14, 'Voltcart Bluetooth Speaker Mini', 'IPX7 portable speaker, 12 h playback', 1499.00, 150),
  (15, 'Voltcart USB-C Hub 6-in-1', 'HDMI 4K, 3x USB-A, SD, PD 100 W', 2599.00, 90),
  (16, 'Voltcart Portable SSD 512GB', 'USB 3.2 Gen 2 pocket SSD', 4299.00, 75),
  (17, 'Voltcart Screen Guard Kit', 'Tempered glass, 2-pack with applicator', 349.00, 600),
  (18, 'Voltcart Gaming Mouse Pad XL', '900 x 400 mm stitched-edge pad', 699.00, 240),
  (19, 'Voltcart Cable Organiser Set', 'Velcro ties, clips and sleeve', 449.00, 400),
  (20, 'Voltcart Wireless Charging Pad', '15 W Qi fast wireless charger', 1299.00, 180);

-- 20 orders: 5 DELIVERED, 5 SHIPPED, 5 PLACED, 5 CANCELLED.
-- total_amount = SUM(quantity * unit_price) of the order's items.

INSERT INTO orders (id, user_id, status, order_date, total_amount) VALUES
  (1, 1, 'DELIVERED', '2026-10-26T13:40:00', 4147.00),
  (2, 2, 'DELIVERED', '2026-10-27T20:00:00', 3898.00),
  (3, 3, 'DELIVERED', '2026-11-16T08:55:00', 5048.00),
  (4, 1, 'DELIVERED', '2026-11-12T18:25:00', 3098.00),
  (5, 2, 'DELIVERED', '2026-11-08T18:55:00', 4596.00),
  (6, 3, 'SHIPPED', '2026-11-17T11:55:00', 6896.00),
  (7, 1, 'SHIPPED', '2026-11-18T18:40:00', 1997.00),
  (8, 2, 'SHIPPED', '2026-11-14T08:00:00', 3398.00),
  (9, 3, 'SHIPPED', '2026-11-15T12:40:00', 8195.00),
  (10, 1, 'SHIPPED', '2026-11-10T08:55:00', 449.00),
  (11, 2, 'PLACED', '2026-11-21T15:25:00', 4297.00),
  (12, 3, 'PLACED', '2026-11-21T08:25:00', 1299.00),
  (13, 1, 'PLACED', '2026-11-18T17:40:00', 2997.00),
  (14, 2, 'PLACED', '2026-11-23T17:25:00', 4197.00),
  (15, 3, 'PLACED', '2026-11-20T11:55:00', 4396.00),
  (16, 1, 'CANCELLED', '2026-11-01T14:40:00', 3948.00),
  (17, 2, 'CANCELLED', '2026-10-26T08:25:00', 1598.00),
  (18, 3, 'CANCELLED', '2026-10-27T14:10:00', 4147.00),
  (19, 1, 'CANCELLED', '2026-10-29T13:00:00', 10195.00),
  (20, 2, 'CANCELLED', '2026-10-28T13:00:00', 6098.00);

-- Delivered orders arrived 4 days after they were placed; all other orders have no delivery date.
UPDATE orders SET delivered_at = strftime('%Y-%m-%dT%H:%M:%S', order_date, '+4 days') WHERE status = 'DELIVERED';

-- Order lines

INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES
  (1, 1, 2, 2, 1899.00),
  (2, 1, 17, 1, 349.00),
  (3, 2, 20, 1, 1299.00),
  (4, 2, 15, 1, 2599.00),
  (5, 3, 19, 1, 449.00),
  (6, 3, 6, 1, 4599.00),
  (7, 4, 12, 1, 1799.00),
  (8, 4, 20, 1, 1299.00),
  (9, 5, 13, 2, 799.00),
  (10, 5, 15, 1, 2599.00),
  (11, 5, 4, 1, 399.00),
  (12, 6, 2, 2, 1899.00),
  (13, 6, 7, 1, 899.00),
  (14, 6, 10, 1, 2199.00),
  (15, 7, 19, 2, 449.00),
  (16, 7, 11, 1, 1099.00),
  (17, 8, 3, 1, 2299.00),
  (18, 8, 11, 1, 1099.00),
  (19, 9, 3, 1, 2299.00),
  (20, 9, 8, 2, 2499.00),
  (21, 9, 19, 2, 449.00),
  (22, 10, 19, 1, 449.00),
  (23, 11, 4, 2, 399.00),
  (24, 11, 1, 1, 3499.00),
  (25, 12, 20, 1, 1299.00),
  (26, 13, 18, 1, 699.00),
  (27, 13, 4, 1, 399.00),
  (28, 13, 2, 1, 1899.00),
  (29, 14, 4, 1, 399.00),
  (30, 14, 2, 2, 1899.00),
  (31, 15, 14, 2, 1499.00),
  (32, 15, 18, 2, 699.00),
  (33, 16, 19, 1, 449.00),
  (34, 16, 1, 1, 3499.00),
  (35, 17, 13, 2, 799.00),
  (36, 18, 17, 1, 349.00),
  (37, 18, 9, 1, 1999.00),
  (38, 18, 12, 1, 1799.00),
  (39, 19, 19, 2, 449.00),
  (40, 19, 8, 2, 2499.00),
  (41, 19, 16, 1, 4299.00),
  (42, 20, 15, 1, 2599.00),
  (43, 20, 1, 1, 3499.00);

-- One payment per order; cancelled orders are REFUNDED

INSERT INTO payments (id, order_id, amount, status, payment_method, transaction_reference, paid_at) VALUES
  (1, 1, 4147.00, 'PAID', 'NETBANKING', 'VC-20261026-0001', '2026-10-26T13:45:00'),
  (2, 2, 3898.00, 'PAID', 'NETBANKING', 'VC-20261027-0002', '2026-10-27T20:05:00'),
  (3, 3, 5048.00, 'PAID', 'CARD', 'VC-20261116-0003', '2026-11-16T09:00:00'),
  (4, 4, 3098.00, 'PAID', 'UPI', 'VC-20261112-0004', '2026-11-12T18:30:00'),
  (5, 5, 4596.00, 'PAID', 'CARD', 'VC-20261108-0005', '2026-11-08T19:00:00'),
  (6, 6, 6896.00, 'PAID', 'UPI', 'VC-20261117-0006', '2026-11-17T12:00:00'),
  (7, 7, 1997.00, 'PAID', 'CARD', 'VC-20261118-0007', '2026-11-18T18:45:00'),
  (8, 8, 3398.00, 'PAID', 'NETBANKING', 'VC-20261114-0008', '2026-11-14T08:05:00'),
  (9, 9, 8195.00, 'PAID', 'CARD', 'VC-20261115-0009', '2026-11-15T12:45:00'),
  (10, 10, 449.00, 'PAID', 'NETBANKING', 'VC-20261110-0010', '2026-11-10T09:00:00'),
  (11, 11, 4297.00, 'PAID', 'UPI', 'VC-20261121-0011', '2026-11-21T15:30:00'),
  (12, 12, 1299.00, 'PAID', 'UPI', 'VC-20261121-0012', '2026-11-21T08:30:00'),
  (13, 13, 2997.00, 'PAID', 'CARD', 'VC-20261118-0013', '2026-11-18T17:45:00'),
  (14, 14, 4197.00, 'PAID', 'CARD', 'VC-20261123-0014', '2026-11-23T17:30:00'),
  (15, 15, 4396.00, 'PAID', 'CARD', 'VC-20261120-0015', '2026-11-20T12:00:00'),
  (16, 16, 3948.00, 'REFUNDED', 'NETBANKING', 'VC-20261101-0016', '2026-11-01T14:45:00'),
  (17, 17, 1598.00, 'REFUNDED', 'NETBANKING', 'VC-20261026-0017', '2026-10-26T08:30:00'),
  (18, 18, 4147.00, 'REFUNDED', 'CARD', 'VC-20261027-0018', '2026-10-27T14:15:00'),
  (19, 19, 10195.00, 'REFUNDED', 'CARD', 'VC-20261029-0019', '2026-10-29T13:05:00'),
  (20, 20, 6098.00, 'REFUNDED', 'NETBANKING', 'VC-20261028-0020', '2026-10-28T13:05:00');

-- Returns

INSERT INTO returns (id, order_id, order_item_id, user_id, reason, status, requested_at, resolved_at, admin_id) VALUES
  (1, 1, 1, 1, 'Stopped holding a charge after one week of use.', 'PENDING', '2026-11-06T10:20:00', NULL, NULL),
  (2, 2, 3, 2, 'Received the wrong colour.', 'APPROVED', '2026-11-06T15:00:00', '2026-11-07T11:30:00', 4);

-- Support tickets linked to the returns

INSERT INTO tickets (id, user_id, return_id, subject, status, thread_id, user_email, created_at, updated_at) VALUES
  (1, 1, 1, 'Return request for Voltcart PowerCube 20000', 'OPEN', '3f6c2a9e-8d41-4b7a-9e21-5c0d7b1a4e10', 'priya.sharma@example.com', '2026-11-06T10:25:00', '2026-11-06T10:25:00'),
  (2, 2, 2, 'Return request for Voltcart Wireless Charging Pad', 'RESOLVED', 'b82e7d14-0c55-4f3a-a6d9-91e2f4c7b358', 'rahul.verma@example.com', '2026-11-06T15:05:00', '2026-11-07T11:35:00');

-- Sample conversation (optional; chat history itself lives in the LangGraph checkpointer)

INSERT INTO conversations (id, user_id, role, started_at, status) VALUES
  (1, 1, 'customer', '2026-11-19T09:00:00', 'ACTIVE');

INSERT INTO messages (id, conversation_id, sender_type, content, created_at) VALUES
  (1, 1, 'user', 'Where is my latest order?', '2026-11-19T09:01:00'),
  (2, 1, 'bot', 'Order #7 is SHIPPED and on its way to you.', '2026-11-19T09:01:20');

-- Email log

INSERT INTO email_logs (id, user_id, email, subject, body_preview, email_type, sent_at) VALUES
  (1, 2, 'rahul.verma@example.com', 'Your Voltcart return is approved', 'Good news: your return for Voltcart Wireless Charging Pad has been approved.', 'RETURN_APPROVED', '2026-11-07T11:40:00');
