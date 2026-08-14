"""
auto_post.py
------------
Generates ready-to-post social media content after each trade.
Copies tweet text to clipboard and generates a shareable image.

Usage:
  py auto_post.py           # Generate post from latest trade
  py auto_post.py --all     # Generate summary post (weekly)
"""

import csv
import sys
import os
from pathlib import Path
from datetime import datetime, timezone

try:
    import pyperclip
    HAS_CLIPBOARD = True
except ImportError:
    HAS_CLIPBOARD = False

RESULTS_FILE = Path(__file__).parent / "logs" / "trade_results.csv"
IMAGE_DIR = Path(__file__).parent / "posts"


def load_trades():
    if not RESULTS_FILE.exists():
        return []
    with open(RESULTS_FILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def get_stats(trades):
    wins = [t for t in trades if t["outcome"] == "WIN"]
    total = len(trades)
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0

    # Correct P&L for shorts
    total_pnl = 0
    for t in trades:
        entry = float(t["entry"])
        exit_p = float(t["exit_price"])
        tp = float(t["take_profit"])
        if tp < entry:  # short
            pnl = (entry - exit_p) * (8000 / entry)
        else:  # long
            pnl = (exit_p - entry) * (8000 / entry)
        total_pnl += pnl

    return {
        "total": total,
        "wins": len(wins),
        "losses": total - len(wins),
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
    }


def generate_trade_post(trade):
    """Generate a tweet for a single trade result."""
    entry = float(trade["entry"])
    exit_p = float(trade["exit_price"])
    tp = float(trade["take_profit"])
    outcome = trade["outcome"]
    duration = float(trade["duration_min"])

    # Determine direction and correct P&L
    if tp < entry:  # short
        direction = "SHORT"
        pnl_pts = entry - exit_p
    else:  # long
        direction = "LONG"
        pnl_pts = exit_p - entry

    troy_oz = 8000 / entry
    pnl_usd = round(pnl_pts * troy_oz, 2)

    # Format duration
    if duration > 60:
        dur_str = f"{duration/60:.1f}h"
    else:
        dur_str = f"{duration:.0f}min"

    # Load stats
    trades = load_trades()
    stats = get_stats(trades)

    if outcome == "WIN":
        emoji = "🟢" if direction == "LONG" else "🔴"
        tweet = (
            f"{emoji} Gold {direction} → TP HIT ✅\n"
            f"\n"
            f"Entry: ${entry:.2f}\n"
            f"Exit: ${exit_p:.2f}\n"
            f"P&L: +${pnl_usd:.2f} ({dur_str})\n"
            f"\n"
            f"Track record: {stats['total']} trades | {stats['win_rate']}% win rate\n"
            f"\n"
            f"Free signals → t.me/goldpulse14\n"
            f"#gold #xauusd #trading #signals #forex"
        )
    else:
        tweet = (
            f"❌ Gold {direction} → SL hit\n"
            f"\n"
            f"Entry: ${entry:.2f}\n"
            f"Exit: ${exit_p:.2f}\n"
            f"P&L: -${abs(pnl_usd):.2f}\n"
            f"\n"
            f"Part of the game. {stats['win_rate']}% win rate over {stats['total']} trades.\n"
            f"\n"
            f"Free signals → t.me/goldpulse14\n"
            f"#gold #xauusd #trading"
        )

    return tweet


def generate_weekly_post(trades):
    """Generate a weekly summary tweet."""
    stats = get_stats(trades)

    pnl_sign = "+" if stats["total_pnl"] >= 0 else ""

    tweet = (
        f"📊 GoldPulse Weekly Recap\n"
        f"\n"
        f"Trades: {stats['total']}\n"
        f"Win rate: {stats['win_rate']}%\n"
        f"P&L: {pnl_sign}${stats['total_pnl']:.2f}\n"
        f"Wins: {stats['wins']} | Losses: {stats['losses']}\n"
        f"\n"
        f"100% algorithmic. No emotions. No guessing.\n"
        f"\n"
        f"Join free → t.me/goldpulse14\n"
        f"Track record → goldpulse.datavoxy.com\n"
        f"#gold #xauusd #algotrading #forex #trading"
    )

    return tweet


def generate_image(tweet_text, filename="latest_post.png"):
    """Generate a simple share image with trade result."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        os.makedirs(IMAGE_DIR, exist_ok=True)

        fig, ax = plt.subplots(figsize=(6, 4))
        ax.set_facecolor("#0a0a0f")
        fig.patch.set_facecolor("#0a0a0f")

        # Remove axes
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

        # Title
        ax.text(0.5, 0.92, "GoldPulse", ha="center", va="top",
                fontsize=20, fontweight="bold", color="#FFD700")

        # Content (simplified from tweet)
        lines = tweet_text.split("\n")
        # Filter out hashtags and links for image
        content_lines = [l for l in lines if not l.startswith("#") and "t.me" not in l and "datavoxy" not in l and l.strip()]

        y = 0.78
        for line in content_lines[:8]:
            color = "#22c55e" if "✅" in line or "+$" in line else "#ef4444" if "❌" in line or "SL" in line else "#e4e4e7"
            ax.text(0.5, y, line, ha="center", va="top",
                    fontsize=11, color=color, fontfamily="monospace")
            y -= 0.1

        # Footer
        ax.text(0.5, 0.05, "t.me/goldpulse14 | goldpulse.datavoxy.com",
                ha="center", va="bottom", fontsize=8, color="#9ca3af")

        path = IMAGE_DIR / filename
        plt.savefig(path, dpi=150, bbox_inches="tight", facecolor="#0a0a0f")
        plt.close()
        return str(path)

    except ImportError:
        return None


if __name__ == "__main__":
    trades = load_trades()

    if not trades:
        print("No trades found.")
        sys.exit(1)

    if "--all" in sys.argv or "--weekly" in sys.argv:
        tweet = generate_weekly_post(trades)
        img_file = "weekly_post.png"
    else:
        tweet = generate_trade_post(trades[-1])
        img_file = "latest_post.png"

    # Generate image
    img_path = generate_image(tweet, img_file)

    # Output
    print("\n" + "=" * 50)
    print("COPY-PASTE THIS TO TWITTER/X:")
    print("=" * 50)
    print(tweet)
    print("=" * 50)

    if img_path:
        print(f"\n📷 Image saved: {img_path}")
        print("   Attach this image to your tweet for more engagement.")

    # Copy to clipboard if available
    if HAS_CLIPBOARD:
        pyperclip.copy(tweet)
        print("\n✅ Copied to clipboard! Just paste in Twitter.")
    else:
        print("\n💡 Install pyperclip for auto-clipboard: pip install pyperclip")
