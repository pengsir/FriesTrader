#!/usr/bin/env python3
"""Exit-rule backtest on the live entries recorded in trade_log.jsonl.

Question answered: holding the SAME entries (symbol, date, fill price, $ size)
fixed, how would different stop-loss / take-profit / trailing / time-stop
rules have performed on daily OHLC bars?

It does NOT test entry rules, and it is a tiny sample (~12 entries, one
month, one market regime). Treat results as a sanity check on mechanics,
not as proof that any parameter is "optimal".

Data: backtest/prices.json = {SYMBOL: [[YYYY-MM-DD, open, high, low, close], ...]}
(fetched with get_equity_historicals, interval=day, split-adjusted).

Usage:
  python3 scripts/backtest_exits.py                 # scenarios + validation
  python3 scripts/backtest_exits.py --grid          # parameter grid
  python3 scripts/backtest_exits.py --trades        # per-trade detail
"""
import argparse
import itertools
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- data

def load_prices(path):
    raw = json.load(open(path))
    return {s: [dict(d=b[0], o=b[1], h=b[2], l=b[3], c=b[4]) for b in bars]
            for s, bars in raw.items()}


def load_entries(path):
    """Live buy fills. Top-ups of an open symbol are merged into avg cost."""
    entries = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if (r.get("stage") == "order" and r.get("side") == "buy"
                and r.get("mode") == "live" and r.get("placed")
                and r.get("fill_price")):
            e = entries.setdefault(r["symbol"], dict(symbol=r["symbol"], date=r["date"], qty=0.0, cost=0.0))
            e["qty"] += r["fill_quantity"]
            e["cost"] += r["fill_quantity"] * r["fill_price"]
    out = []
    for e in entries.values():
        e["price"] = e["cost"] / e["qty"]
        out.append(e)
    return sorted(out, key=lambda x: x["date"])


def actual_exits(path):
    """Actual sells from the log, for validating the baseline simulation."""
    res = {}
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if (r.get("stage") == "order" and r.get("side") == "sell"
                and r.get("mode") == "live" and r.get("placed")):
            res.setdefault(r["symbol"], []).append((r["date"], r["fill_price"], r["fill_quantity"], r.get("reason")))
    return res

# ---------------------------------------------------------------- indicators

def stdev_ret(bars, i, n=20):
    """Sample stdev of the n daily close-to-close returns ending at bar i (inclusive)."""
    lo = max(1, i - n + 1)
    rets = [bars[k]["c"] / bars[k - 1]["c"] - 1 for k in range(lo, i + 1)]
    return statistics.stdev(rets) if len(rets) >= 2 else None


def atr(bars, i, n=14):
    lo = max(1, i - n + 1)
    trs = []
    for k in range(lo, i + 1):
        pc = bars[k - 1]["c"]
        trs.append(max(bars[k]["h"] - bars[k]["l"], abs(bars[k]["h"] - pc), abs(bars[k]["l"] - pc)))
    return sum(trs) / len(trs) if trs else None

# ---------------------------------------------------------------- scenarios

def scenario(**kw):
    base = dict(
        name="",
        stop_mode="vol",        # vol | atr | fixed
        stop_mult=2.5,          # x stdev (vol) or x ATR14 (atr)
        stop_min=0.05, stop_max=0.15, stop_fixed=0.07,
        check="open",           # open = once a day at the open (current process) | intraday
        tp=[(0.15, 0.25), (0.30, 0.25), (0.50, 0.25)],
        be_gain=None,           # arm breakeven stop once max close gain >= this
        trail_atr=None,         # trailing stop = highest close - k x ATR14 (armed with be_gain, or at once if be_gain None)
        time_days=None, time_min_gain=0.02,  # exit at close if held >= N bars and gain < min
    )
    base.update(kw)
    return base


SCENARIOS = [
    scenario(name="0 baseline (current rules)"),
    scenario(name="1 intraday stop check", check="intraday"),
    scenario(name="2 +TP 6/12/20 (1/3 each)", check="intraday",
             tp=[(0.06, 1 / 3), (0.12, 1 / 3), (0.20, 1 / 3)]),
    scenario(name="3 +breakeven@+4%", check="intraday", be_gain=0.04,
             tp=[(0.06, 1 / 3), (0.12, 1 / 3), (0.20, 1 / 3)]),
    scenario(name="4 +trail 2xATR", check="intraday", be_gain=0.04, trail_atr=2.0,
             tp=[(0.06, 1 / 3), (0.12, 1 / 3), (0.20, 1 / 3)]),
    scenario(name="5 +time stop 10d<+2%", check="intraday", be_gain=0.04, trail_atr=2.0,
             time_days=10, tp=[(0.06, 1 / 3), (0.12, 1 / 3), (0.20, 1 / 3)]),
    scenario(name="6 full plan, ATR stop 2xATR [5%,10%]", check="intraday", stop_mode="atr",
             stop_mult=2.0, stop_min=0.05, stop_max=0.10, be_gain=0.04, trail_atr=2.0,
             time_days=10, tp=[(0.06, 1 / 3), (0.12, 1 / 3), (0.20, 1 / 3)]),
    scenario(name="7 plan 6 w/o TP tiers (trail only)", check="intraday", stop_mode="atr",
             stop_mult=2.0, stop_min=0.05, stop_max=0.10, be_gain=0.04, trail_atr=2.0,
             time_days=10, tp=[]),
]

# ---------------------------------------------------------------- simulation

def initial_stop_pct(sc, bars, i_entry, i_now=None):
    """i_entry = index of entry bar. Uses data strictly before the entry bar
    (known the evening before) for atr; vol mode is recomputed daily like the live process."""
    if sc["stop_mode"] == "fixed":
        return sc["stop_fixed"]
    if sc["stop_mode"] == "vol":
        j = (i_now if i_now is not None else i_entry) - 1   # last completed close
        sd = stdev_ret(bars, j, 20)
        if sd is None:
            return sc["stop_fixed"]
        return min(max(sc["stop_mult"] * sd, sc["stop_min"]), sc["stop_max"])
    a = atr(bars, i_entry - 1, 14)
    return min(max(sc["stop_mult"] * a / bars[i_entry - 1]["c"], sc["stop_min"]), sc["stop_max"])


def simulate(entry, bars, sc):
    """Returns dict(pnl, ret, exit_date, exit_reason, legs, open). $ P&L on entry notional."""
    idx = {b["d"]: k for k, b in enumerate(bars)}
    i0 = idx[entry["date"]]
    ep = entry["price"]
    notional = entry["cost"]
    remaining = 1.0
    realized = 0.0           # fraction-weighted return
    fired = set()
    legs = []
    hi_close = ep
    armed = False
    reason, exit_date = "open_at_end", bars[-1]["d"]

    def sell(frac, px, d, why):
        nonlocal remaining, realized
        frac = min(frac, remaining)
        realized += frac * (px / ep - 1)
        remaining -= frac
        legs.append((d, round(px, 2), round(frac, 3), why))

    for i in range(i0, len(bars)):
        b = bars[i]
        held = i - i0  # 0 on the entry bar
        # --- current stop level (from info known at start of the bar)
        if i == i0:
            stop_px = ep * (1 - initial_stop_pct(sc, bars, i0))
        else:
            stop_pct = initial_stop_pct(sc, bars, i0, i) if sc["stop_mode"] == "vol" else (ep - base_stop) / ep
            base_stop = ep * (1 - stop_pct)
            stop_px = base_stop
            if armed:
                stop_px = max(stop_px, ep * 1.002)
                if sc["trail_atr"]:
                    a = atr(bars, i - 1, 14)
                    stop_px = max(stop_px, hi_close - sc["trail_atr"] * a)
        if i == i0:
            base_stop = stop_px
        # --- the open (current process runs here ~9:37 ET); skip entry bar's own open
        if i > i0:
            if b["o"] <= stop_px:
                sell(remaining, b["o"], b["d"], "stop@open")
                reason, exit_date = "stop", b["d"]
                break
            for t, (g, f) in enumerate(sc["tp"]):
                if t not in fired and b["o"] >= ep * (1 + g):
                    fired.add(t)
                    sell(f, b["o"], b["d"], f"tp{t + 1}@open")
        # --- intraday (only for scenarios that monitor intraday)
        if sc["check"] == "intraday":
            if b["l"] <= stop_px:
                sell(remaining, stop_px, b["d"], "stop@intraday")
                reason, exit_date = "stop", b["d"]
                break
            for t, (g, f) in enumerate(sc["tp"]):
                if t not in fired and b["h"] >= ep * (1 + g):
                    fired.add(t)
                    sell(f, ep * (1 + g), b["d"], f"tp{t + 1}@intraday")
        if remaining <= 1e-9:
            reason, exit_date = "all_tp", b["d"]
            break
        # --- end of day updates
        hi_close = max(hi_close, b["c"])
        if sc["be_gain"] is not None and hi_close / ep - 1 >= sc["be_gain"]:
            armed = True
        if sc["be_gain"] is None and sc["trail_atr"]:
            armed = True
        if sc["time_days"] and held >= sc["time_days"] and b["c"] / ep - 1 < sc["time_min_gain"]:
            sell(remaining, b["c"], b["d"], "time_stop")
            reason, exit_date = "time_stop", b["d"]
            break
    open_flag = remaining > 1e-9
    if open_flag:  # mark remainder at the last close
        sell(remaining, bars[-1]["c"], bars[-1]["d"], "mark")
    return dict(pnl=realized * notional, ret=realized, exit_date=exit_date,
                reason=reason, legs=legs, open=open_flag, notional=notional)


def run(sc, entries, prices, risk_budget=None):
    rows, total = [], 0.0
    for e in entries:
        bars = prices[e["symbol"]]
        r = simulate(e, bars, sc)
        scale = 1.0
        if risk_budget:  # size so a full stop-out loses ~risk_budget, never above actual size
            sp = initial_stop_pct(sc, bars, [b["d"] for b in bars].index(e["date"]))
            scale = min(1.0, risk_budget / (sp * e["cost"]))
        r["pnl"] *= scale
        r["scale"] = scale
        r["symbol"], r["entry_date"] = e["symbol"], e["date"]
        rows.append(r)
        total += r["pnl"]
    return rows, total

# ---------------------------------------------------------------- reporting

def summarize(rows):
    closed = [r for r in rows if not r["open"]]
    wins = [r for r in rows if r["pnl"] > 0.005]
    worst = min(r["pnl"] for r in rows)
    gross_w = sum(r["pnl"] for r in rows if r["pnl"] > 0)
    gross_l = -sum(r["pnl"] for r in rows if r["pnl"] < 0)
    pf = gross_w / gross_l if gross_l else float("inf")
    return len(closed), len(wins), worst, pf


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", default=os.path.join(ROOT, "backtest", "prices.json"))
    ap.add_argument("--log", default=os.path.join(ROOT, "trade_log.jsonl"))
    ap.add_argument("--trades", action="store_true", help="print per-trade legs for every scenario")
    ap.add_argument("--grid", action="store_true", help="run a parameter grid around the proposed plan")
    ap.add_argument("--risk-budget", type=float, default=None,
                    help="also report P&L with risk-based sizing: $ lost on a full stop-out (e.g. 8)")
    a = ap.parse_args()

    prices = load_prices(a.prices)
    entries = load_entries(a.log)
    last = prices[entries[0]["symbol"]][-1]["d"]
    print(f"entries: {len(entries)} live buys, data through {last}; open positions marked at last close\n")

    # ---- validation: baseline vs what really happened
    base_rows, base_total = run(SCENARIOS[0], entries, prices)
    act = actual_exits(a.log)
    print("VALIDATION  baseline sim vs actual live exits (stop-outs only)")
    print(f"{'sym':5} {'sim exit':>10} {'sim px':>8} | {'actual exit':>11} {'actual px':>9}")
    for r in base_rows:
        if r["reason"] == "stop":
            sx = r["legs"][-1]
            ax = [x for x in act.get(r["symbol"], []) if x[3] == "stop_loss"]
            at = f"{ax[0][0]:>11} {ax[0][1]:>9.2f}" if ax else f"{'-':>11} {'-':>9}"
            print(f"{r['symbol']:5} {sx[0]:>10} {sx[1]:>8.2f} | {at}")
    print("(differences = live process checks ~9:37 ET price, sim uses the open bar; and live also trimmed WEC/REYN.)\n")

    # ---- scenarios
    print(f"{'scenario':42} {'P&L $':>8} {'ret %':>7} {'wins':>6} {'worst $':>8} {'PF':>5}" +
          (f" {'P&L$ risk-sized':>16}" if a.risk_budget else ""))
    tot_notional = sum(e["cost"] for e in entries)
    for sc in SCENARIOS:
        rows, tot = run(sc, entries, prices)
        n, w, worst, pf = summarize(rows)
        line = f"{sc['name']:42} {tot:8.2f} {100 * tot / tot_notional:7.2f} {w:>3}/{len(rows):<2} {worst:8.2f} {pf:5.2f}"
        if a.risk_budget:
            line += f" {run(sc, entries, prices, a.risk_budget)[1]:16.2f}"
        print(line)
        if a.trades:
            for r in rows:
                legs = "; ".join(f"{d[5:]} {why}@{px}x{f}" for d, px, f, why in r["legs"])
                print(f"     {r['symbol']:5} {r['pnl']:7.2f}  {legs}")
    print(f"\n(total notional deployed ${tot_notional:.0f}; P&L ignores the WEC/REYN trims and any top-ups)")

    # ---- grid
    if a.grid:
        print("\nGRID  intraday + breakeven + trail + 10d time stop; tp = 3 tiers 1/3 each")
        print(f"{'stop_mult':>9} {'min':>5} {'max':>5} {'be':>5} {'trail':>6} {'tp1':>5} {'P&L $':>8} {'worst':>7} {'wins':>5}")
        res = []
        for mult, smin, smax, be, tr, tp1 in itertools.product(
                [1.5, 2.0, 2.5, 3.0], [0.04, 0.05, 0.06], [0.08, 0.10, 0.12],
                [0.03, 0.04, 0.06], [1.5, 2.0, 3.0], [0.05, 0.06, 0.08]):
            if smin >= smax:
                continue
            sc = scenario(check="intraday", stop_mode="atr", stop_mult=mult, stop_min=smin, stop_max=smax,
                          be_gain=be, trail_atr=tr, time_days=10,
                          tp=[(tp1, 1 / 3), (tp1 * 2, 1 / 3), (tp1 * 3.3, 1 / 3)])
            rows, tot = run(sc, entries, prices)
            n, w, worst, pf = summarize(rows)
            res.append((tot, mult, smin, smax, be, tr, tp1, worst, w))
        res.sort(reverse=True)
        pnls = [r[0] for r in res]
        for r in res[:8]:
            print(f"{r[1]:9.1f} {r[2]:5.2f} {r[3]:5.2f} {r[4]:5.2f} {r[5]:6.1f} {r[6]:5.2f} {r[0]:8.2f} {r[7]:7.2f} {r[8]:5d}")
        print("  ...")
        for r in res[-3:]:
            print(f"{r[1]:9.1f} {r[2]:5.2f} {r[3]:5.2f} {r[4]:5.2f} {r[5]:6.1f} {r[6]:5.2f} {r[0]:8.2f} {r[7]:7.2f} {r[8]:5d}")
        print(f"\n  {len(res)} combos: median ${statistics.median(pnls):.2f}, best ${max(pnls):.2f}, worst ${min(pnls):.2f}; "
              f"baseline ${base_total:.2f}.")
        print("  Look at the MEDIAN and the spread, not the best row -- the best row is overfit to 12 trades.")


if __name__ == "__main__":
    sys.exit(main())
