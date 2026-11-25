#!/usr/bin/env python3
# Part of FriesTrader (https://github.com/YizhiSong/FriesTrader)
# Copyright (c) 2026 Yizhi Song, MIT License -- see LICENSE
"""Circuit breaker for clustered stop-outs, per risk_rules.json
loss_limits.stop_cluster / PHASE_B_TASK.md Step 7.

Input: the age, in trading days, of every recent stop_loss trigger
(0 = today, 1 = previous trading day, ...). The caller derives these from
trade_log.jsonl `"stage": "stop_loss", "triggered": true` entries (one per
distinct symbol+date) -- including this cycle's own triggers.

Rules:
  * cluster = at least --max-stops stops with age < --window-trading-days
  * while a cluster exists, new entries are halted until --pause-trading-days
    trading days have passed since the MOST RECENT stop (age >= pause)
  * after that, and while the cluster still sits in the window, new-entry
    dollar sizes are multiplied by --size-multiplier
Exits are never affected.
"""
import argparse
import json


def parse_int_list(s):
    if not s:
        return []
    return [int(x) for x in s.split(",")]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stop-ages", type=parse_int_list, default=[],
                    help="comma-separated ages (trading days ago, 0 = today) of recent stop_loss triggers")
    p.add_argument("--window-trading-days", type=int, required=True)
    p.add_argument("--max-stops", type=int, required=True)
    p.add_argument("--pause-trading-days", type=int, required=True)
    p.add_argument("--size-multiplier", type=float, required=True)
    args = p.parse_args()

    in_window = sorted(a for a in args.stop_ages if 0 <= a < args.window_trading_days)
    cluster = len(in_window) >= args.max_stops

    out = {"stops_in_window": len(in_window), "cluster_active": cluster,
           "entries_halted": False, "size_multiplier": 1.0, "halt_reason": None}
    if cluster:
        most_recent = in_window[0]
        if most_recent < args.pause_trading_days:
            out["entries_halted"] = True
            out["halt_reason"] = (
                f"止损集群熔断 -- 最近 {args.window_trading_days} 个交易日内有 {len(in_window)} 次止损 "
                f"(>= {args.max_stops}),最近一次在 {most_recent} 个交易日前,"
                f"暂停新开仓直至满 {args.pause_trading_days} 个交易日"
            )
        else:
            out["size_multiplier"] = args.size_multiplier
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
