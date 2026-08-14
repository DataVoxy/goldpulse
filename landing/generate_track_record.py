"""
generate_track_record.py
------------------------
Reads trade_results.csv and generates a track_record.json file
for the landing page to display.

Run this after each trade closes, or on a schedule.

Usage:
  python landing/generate_track_record.py
"""

import csv
import json
from pathlib import Path

RESULTS_FILE = Path(__file__).parent.parent / "logs" / "trade_results.csv"
OUTPUT_FILE = Path(__file__).parent / "track_record.json"


def generate():
    if not RESULTS_FILE.exists():
        print("No trade_results.csv found. Nothing to generate.")
        return

    with open(RESULTS_FILE, encoding="utf-8") as f:
        trades = list(csv.DictReader(f))

    if not trades:
        print("No trades in CSV.")
        return

    # Calculate stats (recalculate from corrected trades)
    wins = [t for t in trades if t["outcome"] == "WIN"]
    losses = [t for t in trades if t["outcome"] == "LOSS"]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0

    # Build output
    output = {
        "stats": {},
        "trades": []
    }

    for t in trades:
        # Determine direction: if TP (exit on win) is below entry, it's a short
        entry = float(t["entry"])
        tp = float(t["take_profit"]) if t.get("take_profit") else None
        exit_p = float(t["exit_price"])

        if tp and tp < entry:
            direction = "SHORT"
        elif tp and tp > entry:
            direction = "LONG"
        else:
            # Fallback: if exit < entry on a WIN, it was a short
            direction = "SHORT" if exit_p < entry and t["outcome"] == "WIN" else "LONG"

        # Correct P&L for shorts (in CSV it may be stored incorrectly)
        pnl_usd = float(t["pnl_usd"])
        pnl_pts = float(t["pnl_pts"])
        if direction == "SHORT":
            # For shorts: profit = entry - exit
            correct_pnl_pts = entry - exit_p
            troy_oz = 8000 / entry  # EXPOSURE_USD / entry
            correct_pnl_usd = round(correct_pnl_pts * troy_oz, 2)
        else:
            correct_pnl_pts = exit_p - entry
            troy_oz = 8000 / entry
            correct_pnl_usd = round(correct_pnl_pts * troy_oz, 2)

        output["trades"].append({
            "open_time": t.get("open_time", ""),
            "close_time": t.get("close_time", ""),
            "entry": entry,
            "exit_price": exit_p,
            "outcome": t["outcome"],
            "pnl_pts": round(correct_pnl_pts, 2),
            "pnl_usd": correct_pnl_usd,
            "duration_min": float(t["duration_min"]),
            "direction": direction,
        })

    # Write JSON
    # Calculate corrected stats from output trades
    all_pnl = [t["pnl_usd"] for t in output["trades"]]
    win_pnl = [t["pnl_usd"] for t in output["trades"] if t["outcome"] == "WIN"]
    total_pnl = round(sum(all_pnl), 2)
    avg_win = round(sum(win_pnl) / len(win_pnl), 2) if win_pnl else 0

    output["stats"] = {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
    }

    OUTPUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"Generated {OUTPUT_FILE}")
    print(f"Stats: {total} trades | {win_rate}% win rate | ${total_pnl} P&L")


if __name__ == "__main__":
    generate()
