# Dashboard (read-only)

    python3 dashboard/build.py --open

Reads `trade_log.jsonl` and the git history of `pending_proposals.jsonl`, and writes a self-contained `dashboard/dist/index.html` (gitignored; no server, no network). Re-run after each Phase A/B to refresh. Nothing here is used by Phase A/B.

- **Daily**: pick a date; Phase A funnel, theses, screening detail; Phase B loss limits, risk checks, orders, watchlist sync.
- **History**: pick a date range; KPIs, daily charts, per-symbol counts, order history.
