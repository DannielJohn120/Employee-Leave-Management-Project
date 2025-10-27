-- users table
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('employee','hr')),
  leave_balance REAL DEFAULT 15
);

-- leaves table
CREATE TABLE IF NOT EXISTS leaves (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  days REAL NOT NULL,
  leave_type TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'Pending',
  applied_at TEXT NOT NULL,
  reviewed_by INTEGER,
  reviewed_at TEXT,
  review_comment TEXT,
  FOREIGN KEY(employee_id) REFERENCES users(id),
  FOREIGN KEY(reviewed_by) REFERENCES users(id)
);
