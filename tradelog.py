"""
tradelog.py
-----------
Log your manual eToro trades and generate a weekly performance report.

Usage:
  # Log a trade (run this after you close a trade):
  python tradelog.py log

  # View weekly report:
  python tradelog.py report

  # View all trades:
  python tradelog.py history
"""

import csv
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

LOGFILE = Path(__file__).parent / "trades.csv"
FIELDS  = [
    "date", "session", "entry_price", "sl_price", "tp_price",
    "exit_price", "result", "pnl_usd", "rsi_at_entry",
    "near_support", "macd_confirmed", "notes"
]

# ==========================
# LOG A TRADE
# ==========================
def log_trade():
    print("\n=== Log a Trade ===")
    trade = {}
    trade["date"]           = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    trade["session"]        = input("Session (London/US): ").strip()
    trade["entry_price"]    = input("Entry price ($): ").strip()
    trade["sl_price"]       = input("Stop Loss price ($): ").strip()
    trade["tp_price"]       = input("Take Profit price ($): ").strip()
    trade["exit_price"]     = input("Exit price ($): ").strip()
    trade["result"]         = input("Result (WIN/LOSS/BE): ").strip().upper()
    trade["pnl_usd"]        = input("P&L in USD (e.g. +29.41 or -88.22): ").strip()
    trade["rsi_at_entry"]   = input("RSI at entry: ").strip()
    trade["near_support"]   = input("Was price near support? (Y/N): ").strip().upper()
    trade["macd_confirmed"] = input("MACD confirmed? (Y/N): ").strip().upper()
    trade["notes"]          = input("Notes (optional): ").strip()

    file_exists = LOGFILE.exists()
    with open(LOGFILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow(trade)

    print(f"\nTrade logged. Total trades: {count_trades()}")

# ==========================
# COUNT TRADES
# ==========================
def count_trades():
    if not LOGFILE.exists():
        return 0
    with open(LOGFILE, encoding="utf-8") as f:
        return sum(1 for row in csv.DictReader(f))

# ==========================
# LOAD TRADES
# ==========================
def load_trades():
    if not LOGFILE.exists():
        return []
    with open(LOGFILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))

# ==========================
# WEEKLY REPORT
# ==========================
def weekly_report():
    trades = load_trades()
    if not trades:
        print("No trades logged yet.")
        return

    print("\n========== WEEKLY PERFORMANCE REPORT ==========")

    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    be     = [t for t in trades if t["result"] == "BE"]
    total  = len(trades)

    win_rate = (len(wins) / total * 100) if total > 0 else 0

    total_pnl = 0
    for t in trades:
        try:
            total_pnl += float(t["pnl_usd"])
        except ValueError:
            pass

    print(f"Total trades  : {total}")
    print(f"Wins          : {len(wins)}")
    print(f"Losses        : {len(losses)}")
    print(f"Break Even    : {len(be)}")
    print(f"Win Rate      : {win_rate:.1f}%")
    print(f"Total P&L     : ${total_pnl:.2f}")

    # Break-even win rate at current SL:TP
    # Load from strategy config if available
    try:
        import strategy
        sl_mult = strategy.SL_ATR_MULT
        tp_mult = strategy.TP_ATR_MULT
        ratio   = sl_mult / tp_mult
        breakeven_wr = ratio / (1 + ratio) * 100
        print(f"\nCurrent SL:TP ratio : {ratio:.1f}:1")
        print(f"Break-even win rate : {breakeven_wr:.1f}%")
        if win_rate >= breakeven_wr:
            print(f"Status              : PROFITABLE at current ratio (+{win_rate - breakeven_wr:.1f}% above break-even)")
        else:
            print(f"Status              : LOSING at current ratio ({breakeven_wr - win_rate:.1f}% below break-even)")
            print("Suggestion          : Improve win rate OR tighten SL (reduce SL_ATR_MULT)")
    except ImportError:
        pass

    # Session breakdown
    print("\n--- By Session ---")
    for session in ["London", "US"]:
        s_trades = [t for t in trades if session.lower() in t["session"].lower()]
        s_wins   = [t for t in s_trades if t["result"] == "WIN"]
        if s_trades:
            print(f"  {session}: {len(s_trades)} trades, {len(s_wins)} wins ({len(s_wins)/len(s_trades)*100:.0f}% WR)")

    # Condition analysis
    print("\n--- Entry Condition Analysis ---")
    support_trades = [t for t in trades if t["near_support"] == "Y"]
    support_wins   = [t for t in support_trades if t["result"] == "WIN"]
    if support_trades:
        print(f"  Near support    : {len(support_trades)} trades, {len(support_wins)/len(support_trades)*100:.0f}% WR")

    macd_trades = [t for t in trades if t["macd_confirmed"] == "Y"]
    macd_wins   = [t for t in macd_trades if t["result"] == "WIN"]
    if macd_trades:
        print(f"  MACD confirmed  : {len(macd_trades)} trades, {len(macd_wins)/len(macd_trades)*100:.0f}% WR")

    # Friday optimization suggestions
    print("\n--- Friday Optimization Suggestions ---")
    if total < 5:
        print("  Need at least 5 trades for meaningful analysis.")
    else:
        if win_rate < 60:
            print("  Win rate low — consider tightening RSI_OVERSOLD (e.g. 35 instead of 40)")
        if win_rate > 80:
            print("  Win rate strong — consider improving SL:TP ratio (reduce SL_ATR_MULT)")
        if len(support_trades) > 0 and len(support_wins)/len(support_trades) > 0.8:
            print("  Support entries performing well — keep near_support filter")
        if total_pnl < 0:
            print("  Net negative P&L — review SL_ATR_MULT, current stops may be too wide")

    print("================================================\n")

# ==========================
# HISTORY
# ==========================
def show_history():
    trades = load_trades()
    if not trades:
        print("No trades logged yet.")
        return
    print(f"\n{'Date':<22} {'Session':<8} {'Entry':<8} {'Exit':<8} {'Result':<6} {'P&L':>8}  Notes")
    print("-" * 80)
    for t in trades:
        print(f"{t['date']:<22} {t['session']:<8} {t['entry_price']:<8} {t['exit_price']:<8} {t['result']:<6} {t['pnl_usd']:>8}  {t['notes']}")

# ==========================
# ENTRY POINT
# ==========================
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "log":
        log_trade()
    elif cmd == "report":
        weekly_report()
    elif cmd == "history":
        show_history()
    else:
        print(__doc__)
