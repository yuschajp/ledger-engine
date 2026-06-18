-- Core ledger engine schema for a simplified portfolio accounting system.
-- Mirrors the IBOR/ABOR pattern used by platforms like Geneva and Aladdin:
-- transactions are the source of truth, positions are a derived snapshot
-- rebuilt from transaction history, and GL entries are the accounting-side
-- postings generated from each transaction.

CREATE TABLE portfolios (
    portfolio_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    base_currency    TEXT NOT NULL DEFAULT 'USD',
    strategy         TEXT,
    inception_date   DATE NOT NULL
);

CREATE TABLE securities (
    security_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker           TEXT NOT NULL UNIQUE,
    instrument_type  TEXT NOT NULL,
    currency         TEXT NOT NULL DEFAULT 'USD',
    maturity_date    DATE,
    coupon_rate      REAL
);

CREATE TABLE transactions (
    transaction_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id      INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    security_id       INTEGER NOT NULL REFERENCES securities(security_id),
    trade_date        DATE NOT NULL,
    settle_date       DATE NOT NULL,
    transaction_type  TEXT NOT NULL,
    quantity          REAL NOT NULL,
    price             REAL NOT NULL,
    commission        REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE positions (
    position_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id     INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    security_id      INTEGER NOT NULL REFERENCES securities(security_id),
    as_of_date       DATE NOT NULL,
    quantity         REAL NOT NULL,
    cost_basis       REAL NOT NULL,
    market_value     REAL,
    unrealized_pnl   REAL
);

CREATE TABLE prices (
    price_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id      INTEGER NOT NULL REFERENCES securities(security_id),
    price_date       DATE NOT NULL,
    price            REAL NOT NULL,
    source           TEXT NOT NULL DEFAULT 'manual'
);

CREATE TABLE gl_accounts (
    gl_account_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    account_number   TEXT NOT NULL UNIQUE,
    account_name     TEXT NOT NULL,
    account_type     TEXT NOT NULL
);

CREATE TABLE gl_entries (
    gl_entry_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id   INTEGER REFERENCES transactions(transaction_id),
    gl_account_id    INTEGER NOT NULL REFERENCES gl_accounts(gl_account_id),
    portfolio_id     INTEGER NOT NULL REFERENCES portfolios(portfolio_id),
    entry_date       DATE NOT NULL,
    debit_amount     REAL NOT NULL DEFAULT 0.0,
    credit_amount    REAL NOT NULL DEFAULT 0.0,
    description      TEXT
);
