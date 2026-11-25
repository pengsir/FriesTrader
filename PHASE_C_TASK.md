# Phase C — Intraday Stop-Loss / Take-Profit Sweep (Automated, script-only)

_Part of [FriesTrader](https://github.com/YizhiSong/FriesTrader), Copyright (c) 2026 Yizhi Song, MIT License._

A light, sells-only sweep that runs every `intraday_monitor.interval_minutes`
(60 -- the cloud scheduler's minimum interval) during regular market hours on weekdays, between Phase B's daily run
and the close. It exists because Phase B only evaluates stops once a day at
the open: a backtest on this account's live entries showed intraday
stop checking would have cut the month's losses by about $8 (~16%). It is **not**
a second Phase B: no news search, no thesis, no buys, no top-ups, no
watchlist sync.

Schedule (Pacific): hourly at :05 from 7:05am to 12:05pm weekdays (the
first run is after Phase B's 6:35am run has finished; the last is before
the 1:00pm close). Like Phases A/B, the routine fires on a UTC cron that
covers both daylight-saving offsets and gates itself on Los Angeles time. The scheduler itself lives outside this repo — set it up
wherever Phase A/B are scheduled.

Authorized to place real live orders under exactly the same gate as Phase B
Step 6 (see "Live-order gate" there). That authorization must be given
explicitly, in advance, by whoever operates this pipeline. Do not add,
remove or loosen any gate condition on your own judgment.

## Language

Same rule as Phase B: human-readable `reason`/`notes` in Chinese, every
machine-read field, stage name and script argument in English.

## Step 0 — Load state

1. Read `risk_rules.json` fresh. If `intraday_monitor.enabled` is `false`,
   stop immediately without logging anything.
2. Confirm the regular session is open (a fresh `get_equity_quotes` on any
   held symbol with a `last_trade_price` timestamp from the last few
   minutes during regular hours). Otherwise stop — never trade pre/post-market.
3. Read `trade_log.jsonl`. Dry-run cycle count and the live-order gate are
   evaluated exactly as in Phase B Step 0/Step 6.
4. `get_equity_positions` (account from `risk_rules.json`). No positions →
   stop. Also call `get_equity_orders` and skip any symbol that already has
   an open (unfilled) sell order, so a position is never double-sold.

## Step 1 — Stop-loss and take-profit checks

For each open position run the **identical** checks to Phase B Step 5, using
the same scripts and the same input gathering (fresh quote, trailing-high
inputs once a tier has fired, daily closes for the volatility-scaled stop):

- `python3 scripts/stop_loss.py …` — see Phase B Step 5 for the full call.
- `python3 scripts/take_profit.py …` — `--already-fired` comes from the
  `take_profit` lines logged since the position last closed to zero.

If either script fails, skip that position this sweep and log one line
`"stage": "stop_loss"` (or `take_profit`) with `"triggered": false,
"action": "halt_entries_check_manually", "source": "phase_c"` and a
Chinese `notes`. Do not hand-compute.

**Log only what matters.** A position that triggers nothing writes no
line (an hourly sweep would otherwise bloat `trade_log.jsonl` with
hundreds of identical hold lines). Triggers and failures are logged with
the same fields as Phase B's lines plus `"source": "phase_c"`.

## Step 2 — Sell

For every stop-loss trigger and fired take-profit tier, run Phase B Step
6's exact procedure (`review_equity_order` first → live-order gate →
`place_equity_order` → confirm via `get_equity_orders` → log the `order`
entry including the wash-sale flag logic). A stop-loss is a full-position
sell; a take-profit tier sells `quantity_sold` (the last tier sells the
whole remaining quantity). Add `"source": "phase_c"` and the usual
`proposal_date` (null for stop/take-profit, as in Phase B).

`execution.mode == "dry_run"`: log `"would_execute": true` and never call
`place_equity_order`.

These lines feed Phase B's re-entry lock, the stop-cluster breaker
(`scripts/stop_cluster.py`) and the take-profit already-fired lookup
exactly like Phase B's own.

## Step 3 — Save (only if something was logged)

```
git add trade_log.jsonl
git commit -m "Phase C sweep <date> <timestamp>"
git push origin HEAD:main
```
If the push is rejected, `git pull --rebase origin main` once, push once
more, then stop and report the error. Never run `git checkout`,
`git switch`, `git reset`, `git stash`, `git merge`, `git branch -f` or a
force-push. If nothing was logged, make no commit.

## Hard rules

- Sells only. Never `place_equity_order` for a buy; never touch watchlists.
- Never change `execution.mode` or any `risk_rules.json` value.
- Never place an order unless the live-order gate is open at that moment.
- A good thesis never cancels a stop-loss or a take-profit tier.
- If positions, quotes or history can't be retrieved, do nothing this
  sweep and log the failure; Phase B's daily check is still the backstop.
