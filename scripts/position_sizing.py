#!/usr/bin/env python3
# Part of FriesTrader (https://github.com/YizhiSong/FriesTrader)
# Copyright (c) 2026 Yizhi Song, MIT License -- see LICENSE
"""Run Step 5's per-candidate position-sizing/risk_check math over an
already priority-sorted candidate list, per risk_rules.json/
PHASE_B_TASK.md.

Candidates are processed in the order given (the caller must have
already applied the conviction/risk_flags/pct_below_52wk_high sort) so
that cash_remaining and concurrent_positions_after compound correctly
across the list, the same way a human working top-to-bottom down a
priority list would.

Candidate JSON is read from stdin, an array of objects:
  {"symbol": "GTLB", "group": "new", "conviction": "high"}
  {"symbol": "AMZN", "group": "held", "conviction": "high", "current_position_value": 100.39}
`group` is "new" or "held". `current_position_value` is required for
"held" candidates only.

Optional per-candidate fields for "new" candidates:
  "stop_pct": 0.062   -- the volatility-scaled stop this entry would carry
                         (scripts/stop_loss.py --compute-stop-pct-only);
                         with --risk-per-trade-pct it caps the dollar size so a
                         full stop-out loses at most that share of total_value
  "sector": "Health Care" -- for --max-per-sector (a missing sector skips that check)

Every optional flag below defaults to "off", so older invocations still work.
"""
import argparse
import json
import sys


def parse_pct_map(s):
    out = {}
    for part in s.split(","):
        tier, pct = part.split(":")
        out[tier] = float(pct)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--total-value", type=float, required=True)
    p.add_argument("--cash-start", type=float, required=True,
                    help="live cash balance (get_portfolio.cash) at the start of Step 5")
    p.add_argument("--concurrent-positions-start", type=int, required=True,
                    help="live open position count at the start of Step 5, after any sells this cycle resolved")
    p.add_argument("--entries-halted", action="store_true",
                    help="set if the loss-limit check (or a stop-loss/take-profit script failure) halted entries this cycle")
    p.add_argument("--max-position-pct", type=float, required=True,
                    help="risk_rules.json position_sizing.max_position_pct_of_account")
    p.add_argument("--max-concurrent-positions", type=int, required=True)
    p.add_argument("--min-cash-buffer-pct", type=float, required=True)
    p.add_argument("--min-top-up-usd", type=float, required=True)
    p.add_argument("--min-top-up-pct-of-target", type=float, required=True)
    p.add_argument("--conviction-pct", type=parse_pct_map, required=True,
                    help='fixed conviction-tier sizing table, e.g. "high:0.20,medium:0.12,low:0.06"')
    p.add_argument("--top-ups-disabled", action="store_true",
                    help="risk_rules.json position_sizing.top_ups_enabled == false: reject every held candidate")
    p.add_argument("--risk-per-trade-pct", type=float, default=None,
                    help="position_sizing.risk_per_trade_pct_of_account; needs per-candidate stop_pct")
    p.add_argument("--size-multiplier", type=float, default=1.0,
                    help="stop_cluster.py size_multiplier, applied to new-entry dollar sizes")
    p.add_argument("--max-new-per-day", type=int, default=None)
    p.add_argument("--new-today", type=int, default=0,
                    help="new entries already bought / would-be-bought earlier today")
    p.add_argument("--max-new-per-week", type=int, default=None)
    p.add_argument("--new-this-week", type=int, default=0,
                    help="new entries already bought / would-be-bought this Mon-Fri week (incl. today)")
    p.add_argument("--max-per-sector", type=int, default=None)
    p.add_argument("--held-sectors", default="",
                    help="'|'-separated sector of every currently open position (unknown ones omitted)")
    args = p.parse_args()

    try:
        candidates = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"invalid candidates JSON on stdin: {e}"}), file=sys.stderr)
        sys.exit(1)

    cash_remaining = args.cash_start
    concurrent_positions_after = args.concurrent_positions_start
    min_cash_buffer = args.min_cash_buffer_pct * args.total_value

    results = []
    new_today = args.new_today
    new_this_week = args.new_this_week
    sector_counts = {}
    for sec in (x for x in args.held_sectors.split("|") if x):
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    for c in candidates:
        symbol = c["symbol"]
        group = c["group"]
        conviction = c["conviction"]
        conviction_pct = args.conviction_pct[conviction]

        if args.entries_halted:
            halted_result = {
                "symbol": symbol, "group": group, "conviction": conviction,
                "passed": False,
                "reason": "亏损限额熔断 — 当日/当周回撤已触及上限",
            }
            if group == "held":
                halted_result["position_action"] = "top_up"
            results.append(halted_result)
            continue

        if group == "new":
            dollar_amount = round(conviction_pct * args.total_value, 2)
            sized_by = "conviction"
            stop_pct = c.get("stop_pct")
            if args.risk_per_trade_pct is not None and stop_pct:
                risk_cap = round(args.risk_per_trade_pct * args.total_value / stop_pct, 2)
                if risk_cap < dollar_amount:
                    dollar_amount = risk_cap
                    sized_by = "risk"
            if args.size_multiplier != 1.0:
                dollar_amount = round(dollar_amount * args.size_multiplier, 2)
            sector = c.get("sector")
            candidate_concurrent_after = concurrent_positions_after + 1
            candidate_cash_after = cash_remaining - dollar_amount

            reasons = []
            if conviction_pct > args.max_position_pct:
                reasons.append(f"仓位 {conviction_pct:.4%} 超过 max_position_pct_of_account ({args.max_position_pct:.4%})")
            if candidate_concurrent_after > args.max_concurrent_positions:
                reasons.append(
                    f"买入后持仓数 ({candidate_concurrent_after}) 超过 "
                    f"max_concurrent_positions ({args.max_concurrent_positions}) — 本周期名额已被 "
                    f"更高优先级的候选占满"
                )
            if candidate_cash_after < min_cash_buffer:
                reasons.append(
                    f"交易后剩余现金 (${candidate_cash_after:.2f}) 将低于 "
                    f"总值的 min_cash_buffer_pct (${min_cash_buffer:.2f})"
                )
            if dollar_amount < 1.00:
                reasons.append(f"买入金额 ${dollar_amount:.2f} 低于券商最低 $1.00")
            if args.max_new_per_day is not None and new_today >= args.max_new_per_day:
                reasons.append(
                    f"今日新开仓已达 max_new_positions_per_day ({args.max_new_per_day})"
                )
            if args.max_new_per_week is not None and new_this_week >= args.max_new_per_week:
                reasons.append(
                    f"本周新开仓已达 max_new_positions_per_week ({args.max_new_per_week})"
                )
            if args.max_per_sector is not None and sector and sector_counts.get(sector, 0) >= args.max_per_sector:
                reasons.append(
                    f"行业 {sector} 已持有 {sector_counts[sector]} 只,达到 "
                    f"max_positions_per_sector ({args.max_per_sector})"
                )

            if reasons:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "reason": "; ".join(reasons),
                    "dollar_amount": dollar_amount, "sized_by": sized_by,
                })
                continue

            cash_remaining = candidate_cash_after
            concurrent_positions_after = candidate_concurrent_after
            new_today += 1
            new_this_week += 1
            if sector:
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
            entry = {
                "symbol": symbol, "group": group, "conviction": conviction,
                "passed": True,
                "dollar_amount": dollar_amount,
                "sized_by": sized_by,
                "concurrent_positions_after": concurrent_positions_after,
                "cash_remaining_after": round(cash_remaining, 2),
                "cash_buffer_after_pct": round(cash_remaining / args.total_value, 6),
            }
            if stop_pct:
                entry["stop_pct"] = stop_pct
            if sector:
                entry["sector"] = sector
            elif args.max_per_sector is not None:
                entry["sector_check"] = "skipped_no_sector"
            results.append(entry)

        elif group == "held":
            if args.top_ups_disabled:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": "加仓已停用 (position_sizing.top_ups_enabled=false)",
                })
                continue
            current_position_value = c["current_position_value"]
            target_size = conviction_pct * args.total_value
            headroom = target_size - current_position_value

            if headroom <= 0:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": "已达到或超过其信念档位的目标仓位 — 不加仓",
                    "current_position_value": round(current_position_value, 2),
                    "target_size": round(target_size, 2),
                    "headroom": round(headroom, 2),
                })
                continue

            ceiling_room = args.max_position_pct * args.total_value - current_position_value
            top_up_amount = round(min(headroom, ceiling_room), 2)
            min_top_up_threshold = round(max(1.00, args.min_top_up_usd,
                                              args.min_top_up_pct_of_target * target_size), 2)

            if top_up_amount < min_top_up_threshold:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": (
                        f"加仓金额 ${top_up_amount:.2f} 低于最小加仓门槛 "
                        f"${min_top_up_threshold:.2f} (券商最低 $1.00、min_top_up_usd "
                        f"${args.min_top_up_usd:.2f}、min_top_up_pct_of_target "
                        f"{args.min_top_up_pct_of_target:.0%} × 目标仓位 ${target_size:.2f}, "
                        f"三者取最高) — 未下单"
                    ),
                    "current_position_value": round(current_position_value, 2),
                    "target_size": round(target_size, 2),
                    "headroom": round(headroom, 2),
                })
                continue

            candidate_cash_after = cash_remaining - top_up_amount
            if candidate_cash_after < min_cash_buffer:
                results.append({
                    "symbol": symbol, "group": group, "conviction": conviction,
                    "passed": False, "position_action": "top_up",
                    "reason": (
                        f"交易后剩余现金 (${candidate_cash_after:.2f}) 将低于 "
                        f"总值的 min_cash_buffer_pct (${min_cash_buffer:.2f})"
                    ),
                    "current_position_value": round(current_position_value, 2),
                    "target_size": round(target_size, 2),
                    "headroom": round(headroom, 2),
                })
                continue

            cash_remaining = candidate_cash_after
            results.append({
                "symbol": symbol, "group": group, "conviction": conviction,
                "passed": True, "position_action": "top_up",
                "current_position_value": round(current_position_value, 2),
                "target_size": round(target_size, 2),
                "headroom": round(headroom, 2),
                "dollar_amount": top_up_amount,
                "cash_remaining_after": round(cash_remaining, 2),
                "cash_buffer_after_pct": round(cash_remaining / args.total_value, 6),
            })

        else:
            print(json.dumps({"error": f"unknown group '{group}' for symbol {symbol}"}), file=sys.stderr)
            sys.exit(1)

    print(json.dumps({
        "results": results,
        "cash_remaining_final": round(cash_remaining, 2),
        "concurrent_positions_after_final": concurrent_positions_after,
    }))


if __name__ == "__main__":
    main()
