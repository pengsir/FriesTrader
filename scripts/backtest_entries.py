#!/usr/bin/env python3
"""Entry-rule backtest on the system's own candidate universe.

Question answered: with the SAME exit rules, does it matter how we enter --
chasing a momentum breakout (current scan), a tighter breakout, or buying a
pullback to the 20-day average -- and does a SPY market filter help?

Signal is evaluated on day t's close (like Phase A after the close); the
order fills at day t+1's open (like Phase B). Exits reuse simulate() from
backtest_exits.py. Data: backtest/universe_prices.json
({SYM: [[date, o, h, l, c, volume], ...]}, daily, split-adjusted).

Caveats (also printed with results):
  * Universe = the ~66 names the live system already surfaced (watchlist +
    scan hits), so there is survivorship / selection bias, and no market-cap,
    profitability or earnings filters are applied.
  * 200-day trend filter needs 200 bars, so the usable window is only ~4 months.
  * Daily bars: intraday order of high/low unknown (stop assumed first).

Usage:
  python3 scripts/backtest_entries.py
  python3 scripts/backtest_entries.py --exit current|proposed --max-positions 8 --size 120
"""
import argparse
import json
import math
import os
import random
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_exits import scenario, simulate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EXITS = {
    "current": scenario(name="current exits"),
    "proposed": scenario(name="proposed exits", check="intraday", time_days=10,
                         tp=[(0.06, 1 / 3), (0.12, 1 / 3), (0.20, 1 / 3)]),
}

# ---------------------------------------------------------------- indicators

def sma(vals, n):
    out, s = [None] * len(vals), 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def rsi14(closes):
    out = [None] * len(closes)
    if len(closes) < 15:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag, al = sum(gains[:14]) / 14, sum(losses[:14]) / 14
    for i in range(14, len(closes)):
        if i > 14:
            ag = (ag * 13 + gains[i - 1]) / 14
            al = (al * 13 + losses[i - 1]) / 14
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def prepare(raw):
    data = {}
    for s, rows in raw.items():
        bars = [dict(d=r[0], o=r[1], h=r[2], l=r[3], c=r[4], v=r[5]) for r in rows]
        c = [b["c"] for b in bars]
        v = [b["v"] for b in bars]
        data[s] = dict(
            bars=bars, idx={b["d"]: i for i, b in enumerate(bars)},
            sma20=sma(c, 20), sma50=sma(c, 50), sma200=sma(c, 200),
            rsi=rsi14(c), v5=sma(v, 5), v30=sma(v, 30),
        )
    return data

# ---------------------------------------------------------------- signals

def features(sd, i, spy, spy_i):
    """Everything known at the close of bar i, or None if not enough history."""
    b = sd["bars"]
    if i < 200 or sd["sma200"][i] is None or sd["rsi"][i] is None or not sd["v30"][i]:
        return None
    c = b[i]["c"]
    hi252 = max(x["c"] for x in b[max(0, i - 251):i + 1])
    ext = c / sd["sma20"][i]
    ret5 = c / b[i - 5]["c"] - 1
    ret60 = c / b[i - 60]["c"] - 1
    spy_ret60 = spy["bars"][spy_i]["c"] / spy["bars"][spy_i - 60]["c"] - 1
    prior_max_ext = max(b[k]["c"] / sd["sma20"][k] for k in range(i - 10, i) if sd["sma20"][k])
    return dict(
        c=c, ext=ext, ret5=ret5, rs60=ret60 - spy_ret60,
        rsi=sd["rsi"][i], vol_ratio=sd["v5"][i] / sd["v30"][i],
        near_hi=c / hi252, above200=c > sd["sma200"][i], above50=c > sd["sma50"][i],
        sma50_rising=sd["sma50"][i] > sd["sma50"][i - 5], prior_max_ext=prior_max_ext,
    )


def sig_current(f):    # mirrors the Robinhood 'Short-term Momentum Breakout' scan (price/vol parts)
    return (f["above200"] and 1.0 <= f["ext"] <= 1.10 and 0.015 <= f["ret5"] <= 0.18
            and 48 <= f["rsi"] <= 73 and f["near_hi"] >= 0.85 and f["vol_ratio"] >= 1.15)


def sig_tight(f):      # same, but entry_extension 5% instead of 10%
    return sig_current(f) and f["ext"] <= 1.05


def sig_pullback(f):   # uptrend, was extended recently, now back at the 20-day average
    return (f["above200"] and f["above50"] and f["sma50_rising"] and f["prior_max_ext"] >= 1.06
            and 0.98 <= f["ext"] <= 1.02 and 40 <= f["rsi"] <= 58 and f["near_hi"] >= 0.85)


def sig_control(f):    # anything in a long-term uptrend, no timing edge
    return f["above200"]


STRATS = [
    ("A current breakout", sig_current, False),
    ("A + SPY>20d filter", sig_current, True),
    ("B tight breakout (<=5% ext)", sig_tight, False),
    ("B + SPY>20d filter", sig_tight, True),
    ("C pullback to 20d", sig_pullback, False),
    ("C + SPY>20d filter", sig_pullback, True),
    ("Z control (above 200d only)", sig_control, False),
    ("Z + SPY>20d filter", sig_control, True),
]

# ---------------------------------------------------------------- portfolio

def run_strategy(data, spy, dates, sig, mkt_filter, exit_sc, size, max_pos, max_new_day, cooldown, seed, start):
    rng = random.Random(seed)
    spy_sma20 = spy["sma20"]
    trades = []
    open_until = {}       # symbol -> index (in `dates`) when it frees up
    day_of = {d: k for k, d in enumerate(dates)}
    for k, d in enumerate(dates[:-1]):
        if d < start:
            continue
        spy_i = spy["idx"][d]
        regime_up = spy["bars"][spy_i]["c"] > spy_sma20[spy_i] if spy_sma20[spy_i] else False
        if mkt_filter and not regime_up:
            continue
        held = sum(1 for s, u in open_until.items() if u > k)
        slots = (max_pos - held) if max_pos else 99
        if slots <= 0:
            continue
        cands = []
        for s, sd in data.items():
            if s == "SPY" or d not in sd["idx"] or open_until.get(s, -1) + cooldown > k:
                continue
            i = sd["idx"][d]
            if i + 1 >= len(sd["bars"]) or sd["bars"][i + 1]["d"] != dates[k + 1]:
                continue
            f = features(sd, i, spy, spy_i)
            if f and sig(f):
                cands.append((f["rs60"], s, f))
        if not cands:
            continue
        if sig is sig_control:
            rng.shuffle(cands)                      # no ranking edge for the control
        else:
            cands.sort(key=lambda x: -x[0])         # strongest 60d relative strength first
        for _, s, f in cands[:min(slots, max_new_day)]:
            sd = data[s]
            i0 = sd["idx"][d] + 1
            e = dict(symbol=s, date=sd["bars"][i0]["d"], price=sd["bars"][i0]["o"], cost=size)
            r = simulate(e, sd["bars"], exit_sc)
            hold_days = sd["idx"][r["exit_date"]] - i0
            open_until[s] = k + 1 + hold_days + 1
            r.update(symbol=s, entry=e["date"], hold=hold_days, regime_up=regime_up,
                     ext=f["ext"])
            trades.append(r)
    return trades

# ---------------------------------------------------------------- reporting

def stats(trades):
    if not trades:
        return None
    rets = [t["ret"] for t in trades]
    pnl = [t["pnl"] for t in trades]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    n = len(rets)
    mean = sum(rets) / n
    sd = statistics.stdev(rets) if n > 1 else 0
    t_stat = mean / (sd / math.sqrt(n)) if sd else 0.0
    gw, gl = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x < 0)
    return dict(
        n=n, win=len(wins) / n, mean=mean, median=statistics.median(rets),
        avg_win=(sum(wins) / len(wins)) if wins else 0, avg_loss=(sum(losses) / len(losses)) if losses else 0,
        total=sum(pnl), pf=gw / gl if gl else float("inf"), worst=min(rets),
        hold=sum(t["hold"] for t in trades) / n,
        stopped=sum(1 for t in trades if t["reason"] == "stop") / n, t=t_stat,
    )


def fmt(name, s):
    if not s:
        return f"{name:30} {'(no trades)':>8}"
    return (f"{name:30} {int(round(s['n'])):4d} {100 * s['win']:5.0f}% {100 * s['mean']:6.2f}% {100 * s['median']:6.2f}% "
            f"{100 * s['avg_win']:6.2f}% {100 * s['avg_loss']:7.2f}% {s['total']:8.0f} {s['pf']:5.2f} "
            f"{100 * s['worst']:7.1f}% {s['hold']:5.1f} {100 * s['stopped']:5.0f}% {s['t']:6.2f}")


HEAD = (f"{'strategy':30} {'n':>4} {'win':>6} {'mean':>7} {'median':>7} {'avgWin':>7} {'avgLoss':>8} "
        f"{'P&L $':>8} {'PF':>5} {'worst':>8} {'hold':>5} {'stop%':>6} {'t':>6}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prices", default=os.path.join(ROOT, "backtest", "universe_prices.json"))
    ap.add_argument("--exit", choices=list(EXITS), default=None, help="default: run both")
    ap.add_argument("--size", type=float, default=120.0, help="$ per position")
    ap.add_argument("--max-positions", type=int, default=8)
    ap.add_argument("--max-new-per-day", type=int, default=2)
    ap.add_argument("--cooldown", type=int, default=10, help="bars before the same symbol can re-enter")
    ap.add_argument("--seeds", type=int, default=20, help="random seeds averaged for the control rows")
    ap.add_argument("--start", default=None, help="first signal date (default: first date with 200 bars)")
    a = ap.parse_args()

    data = prepare(json.load(open(a.prices)))
    spy = data["SPY"]
    dates = [b["d"] for b in spy["bars"]]
    start = a.start or dates[200]
    print(f"universe: {len(data) - 1} symbols; signals from {start} to {dates[-2]} "
          f"({sum(1 for d in dates if d >= start)} trading days); $ {a.size:.0f}/position, "
          f"max {a.max_positions} open, <= {a.max_new_per_day} new/day, {a.cooldown}-bar re-entry cooldown")
    i0, i1 = spy["idx"][start], len(dates) - 1
    print(f"SPY over the window: {100 * (spy['bars'][i1]['c'] / spy['bars'][i0]['c'] - 1):+.1f}%  "
          f"(close {spy['bars'][i0]['c']:.0f} -> {spy['bars'][i1]['c']:.0f}); "
          f"fraction of days SPY>20d: "
          f"{100 * sum(1 for k in range(i0, i1) if spy['bars'][k]['c'] > spy['sma20'][k]) / (i1 - i0):.0f}%\n")

    for ex_name in ([a.exit] if a.exit else list(EXITS)):
        ex = EXITS[ex_name]
        for label, cap in (("UNCONSTRAINED (every signal taken; isolates entry quality)", 0),
                           (f"PORTFOLIO (max {a.max_positions} open, strongest-RS first)", a.max_positions)):
            print(f"=== exits: {ex_name} | {label}")
            print(HEAD)
            for name, sig, mf in STRATS:
                ctrl = sig is sig_control
                seeds = range(a.seeds) if ctrl else [0]
                allt = []
                for sd_ in seeds:
                    allt.append(run_strategy(data, spy, dates, sig, mf, ex, a.size, cap,
                                             99 if cap == 0 else a.max_new_per_day, a.cooldown, sd_, start))
                if ctrl:
                    ss = [stats(t) for t in allt if t]
                    avg = {k: sum(s[k] for s in ss) / len(ss) for k in ss[0]}
                    print(fmt(name + f" (avg {len(ss)} seeds)", avg))
                else:
                    print(fmt(name, stats(allt[0])))
            print()

    # regime split for the current breakout, so the "bad market" story is visible
    ex = EXITS["current"]
    t = run_strategy(data, spy, dates, sig_current, False, ex, a.size, 0, 99, a.cooldown, 0, start)
    up = [x for x in t if x["regime_up"]]
    dn = [x for x in t if not x["regime_up"]]
    print("=== A current breakout, unconstrained, by SPY regime at signal time (current exits)")
    print(HEAD)
    print(fmt("SPY > 20d", stats(up)))
    print(fmt("SPY <= 20d", stats(dn)))
    ext_hi = [x for x in t if x["ext"] > 1.05]
    ext_lo = [x for x in t if x["ext"] <= 1.05]
    print(fmt("ext 1.05-1.10 of 20d", stats(ext_hi)))
    print(fmt("ext <= 1.05 of 20d", stats(ext_lo)))
    print("\nRead with care: 't' is mean/stderr of per-trade returns; |t| < 2 means indistinguishable from zero.")


if __name__ == "__main__":
    sys.exit(main())
