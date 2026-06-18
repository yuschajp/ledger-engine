# Ledger Engine

A simplified portfolio accounting engine that mirrors the IBOR → ABOR pattern used by institutional platforms like SS&C Geneva and BlackRock Aladdin: transactions are the source of truth, positions are a derived snapshot rebuilt from transaction history rather than mutated in place, and every transaction generates a balanced pair of general ledger postings.

## Why this exists

This project demonstrates the data architecture and systems-integration thinking behind institutional portfolio accounting platforms, the same logic that sits underneath implementation work on systems like Geneva and Aladdin, built from scratch to show the mechanics rather than just describe them.

## Architecture

```mermaid
flowchart TD
    A[Mock data feeds<br/>trades, prices<br/><i>built</i>] --> B[Core ledger engine<br/>positions, GL postings, NAV<br/><i>built</i>]
    B --> C[Reconciliation engine<br/>break detection & classification<br/><i>planned</i>]
    C --> D[AI triage agent<br/>root cause, audit trail, approval<br/><i>planned</i>]
    D --> E[Reports & dashboard<br/>NAV, reconciliation summary<br/><i>planned</i>]
```

## Schema

```mermaid
erDiagram
    PORTFOLIOS ||--o{ TRANSACTIONS : records
    PORTFOLIOS ||--o{ POSITIONS : holds
    PORTFOLIOS ||--o{ GL_ENTRIES : owns
    SECURITIES ||--o{ TRANSACTIONS : traded_in
    SECURITIES ||--o{ POSITIONS : valued_as
    SECURITIES ||--o{ PRICES : priced
    TRANSACTIONS ||--o{ GL_ENTRIES : posts
    GL_ACCOUNTS ||--o{ GL_ENTRIES : classifies

    PORTFOLIOS {
        int portfolio_id PK
        string name
        string base_currency
        string strategy
        date inception_date
    }
    SECURITIES {
        int security_id PK
        string ticker
        string instrument_type
        string currency
        date maturity_date
        float coupon_rate
    }
    TRANSACTIONS {
        int transaction_id PK
        int portfolio_id FK
        int security_id FK
        date trade_date
        date settle_date
        string transaction_type
        float quantity
        float price
        float commission
    }
    POSITIONS {
        int position_id PK
        int portfolio_id FK
        int security_id FK
        date as_of_date
        float quantity
        float cost_basis
        float market_value
        float unrealized_pnl
    }
    PRICES {
        int price_id PK
        int security_id FK
        date price_date
        float price
        string source
    }
    GL_ACCOUNTS {
        int gl_account_id PK
        string account_number
        string account_name
        string account_type
    }
    GL_ENTRIES {
        int gl_entry_id PK
        int transaction_id FK
        int gl_account_id FK
        int portfolio_id FK
        date entry_date
        float debit_amount
        float credit_amount
        string description
    }
```

## Design decisions

Positions are never updated in place. Every row in `positions` is rebuilt from scratch by summing the full transaction history up to a given date, so every position is traceable back to the exact transactions that produced it. This mirrors how a real investment book of record (IBOR) behaves.

Every transaction generates two GL entries, never one, because double-entry accounting requires it. A buy debits the security account and credits cash; a sell does the reverse.

## Getting started

Requires Python 3 only — no external dependencies.

```bash
git clone <your-repo-url>
cd ledger-engine
python3 demo.py
```

Expected output:

```
Position as of 2024-06-01: 1000 units, cost basis $25,500.00

GL entries posted:
  DR $25,510.00  -  Buy 1000 units
  CR $25,510.00  -  Cash settlement for buy
```

## Project structure

```
schema.sql           # table definitions
ledger_engine.py      # core engine: transaction insertion, position recomputation, GL posting
demo.py                # end-to-end example
README.md
```

## Roadmap

- Reconciliation engine comparing internal positions against a mock custodian feed, classifying breaks by type
- AI triage agent that proposes root causes and resolutions for flagged breaks, with a full audit trail and a human approval gate before any break is marked resolved
- Lightweight dashboard showing daily NAV, open breaks, and the audit log
