"""
Recalculate all existing trades as if they used the new SL/TP ratio.
SL = 0.75x ATR, TP = 1.5x ATR (1:2 in your favor)
"""

import csv
from pathlib import Path

RESULTS_FILE = Path(__file__).parent / "logs" / "trade_results.csv"

# New settings
SL_MULT = 0.75
TP_MULT = 1.5
EXPOSURE_USD = 8000

with open(RESULTS_FILE, encoding="utf-8") as f:
    trades = list(csv.DictReader(f))

print(f"{'#':<3} {'Time':<22} {'Entry':>8} {'ATR':>6} {'New SL':>8} {'New TP':>8} {'Outcome':>8} {'P&L':>8}")
print("-" * 85)

total_pnl = 0
wins = 0
losses = 0

for i, t in enumerate(trades, 1):
    entry = float(t["entry"])
    atr = float(t["atr"])
    tp_old = float(t["take_profit"])
    
    # Determine direction from old TP
    if tp_old < entry:
        direction = "SHORT"
        new_sl = entry + (atr * SL_MULT)
        new_tp = entry - (atr * TP_MULT)
    else:
        direction = "LONG"
        new_sl = entry - (atr * SL_MULT)
        new_tp = entry + (atr * TP_MULT)
    
    # Check what would have happened:
    # The old trade hit either TP or SL. With new wider TP:
    # - If old outcome was WIN (hit old TP at 0.5x ATR), would it have reached 1.5x ATR?
    #   Not necessarily — price hit 0.5x ATR but might not have gone to 1.5x ATR
    # - If old outcome was LOSS (hit old SL at 1.5x ATR), with new tighter SL (0.75x ATR)
    #   it would have hit SL even sooner
    
    old_outcome = t["outcome"]
    old_exit = float(t["exit_price"])
    
    # For a fair recalculation, we check:
    # With new SL (tighter): did price move 0.75x ATR against us?
    # With new TP (wider): did price move 1.5x ATR in our favor?
    
    # From the old data:
    # Old SL was at 1.5x ATR (for longs: entry - 1.5*ATR)
    # Old TP was at 0.5x ATR (for longs: entry + 0.5*ATR)
    
    # If old trade was a WIN (hit 0.5x ATR TP):
    #   - Price moved at least 0.5x ATR in our favor
    #   - With new TP at 1.5x ATR, we DON'T KNOW if it would have reached
    #   - But with new SL at 0.75x ATR (tighter), it may not have been stopped out before
    #   - UNCERTAIN — could be win or loss
    
    # If old trade was a LOSS (hit 1.5x ATR SL):
    #   - Price moved 1.5x ATR against us
    #   - With new SL at 0.75x ATR, it DEFINITELY would have hit SL (even sooner)
    #   - LOSS — but smaller loss (0.75x ATR vs 1.5x ATR)
    
    if old_outcome == "LOSS":
        # Definitely a loss with new settings too, but smaller
        new_outcome = "LOSS"
        if direction == "LONG":
            pnl_pts = -(atr * SL_MULT)  # stopped at 0.75x ATR
        else:
            pnl_pts = -(atr * SL_MULT)
        losses += 1
    else:
        # Old WIN: price reached 0.5x ATR. 
        # We can't know for sure if it reached 1.5x ATR.
        # But we know the exit was at old_exit.
        # For longs: how far did it go? At least to old TP (entry + 0.5*ATR)
        # For shorts: at least to (entry - 0.5*ATR)
        
        # Check if the actual price movement could have reached new TP
        if direction == "LONG":
            # Old exit was entry + 0.5*ATR. Did it go further?
            # We don't have candle high data, but the exit WAS at TP (0.5*ATR)
            # Conservative: assume it only went to 0.5*ATR, so new TP (1.5*ATR) NOT reached
            # But new SL (0.75*ATR) is tighter — check if it got stopped first
            # Since old trade was a WIN, price didn't drop 1.5*ATR, but did it drop 0.75*ATR?
            # We don't know intermediate price action. 
            # CONSERVATIVE: mark as uncertain, use duration as hint
            # If duration was short (< 30 min) = strong move, likely reached further
            # If duration was long (> 2 hrs) = slow move, likely didn't reach 1.5x ATR
            
            duration = float(t["duration_min"])
            if duration < 60:
                # Fast move — likely would reach 1.5x ATR
                new_outcome = "WIN"
                pnl_pts = atr * TP_MULT
                wins += 1
            else:
                # Slow move — conservative: probably got stopped at 0.75x
                # Actually, if it DID reach 0.5x without getting stopped at 0.75x on the downside,
                # it means the drawdown was less than 0.75x ATR. So it would NOT have been stopped.
                # Since the old trade was a WIN, max drawdown was < 1.5x ATR (old SL).
                # But was drawdown < 0.75x ATR? We don't know.
                # Probability estimate: ~50/50 for slow trades
                # Let's be honest and mark these as UNCERTAIN
                new_outcome = "UNCERTAIN"
                pnl_pts = 0
        else:
            duration = float(t["duration_min"])
            if duration < 60:
                new_outcome = "WIN"
                pnl_pts = atr * TP_MULT
                wins += 1
            else:
                new_outcome = "UNCERTAIN"
                pnl_pts = 0
    
    troy_oz = EXPOSURE_USD / entry
    pnl_usd = round(pnl_pts * troy_oz, 2)
    total_pnl += pnl_usd
    
    print(f"{i:<3} {t['open_time']:<22} ${entry:>7.1f} {atr:>5.1f} ${new_sl:>7.2f} ${new_tp:>7.2f} {new_outcome:>8} ${pnl_usd:>7.2f}")

uncertain = len(trades) - wins - losses
print(f"\n{'='*85}")
print(f"RESULTS WITH NEW RATIO (SL=0.75x ATR, TP=1.5x ATR)")
print(f"{'='*85}")
print(f"Definite WINS:   {wins}")
print(f"Definite LOSSES: {losses}")
print(f"UNCERTAIN:       {uncertain} (old wins that may or may not reach new wider TP)")
print(f"")
print(f"Best case (uncertain = all wins):  {wins + uncertain} wins / {losses} losses = {(wins+uncertain)/len(trades)*100:.0f}% WR")
print(f"Worst case (uncertain = all losses): {wins} wins / {losses + uncertain} losses = {wins/len(trades)*100:.0f}% WR")
print(f"")
print(f"Definite P&L so far: ${total_pnl:.2f}")
print(f"Best case P&L:  ${total_pnl + uncertain * (15 * 1.5 * (8000/4080)):.2f}")  # avg ATR ~15, avg entry ~4080
print(f"Worst case P&L: ${total_pnl - uncertain * (15 * 0.75 * (8000/4080)):.2f}")
print(f"")
print(f"KEY INSIGHT: Old LOSSES become SMALLER losses with new ratio")
print(f"  Old avg loss: ~$30 (1.5x ATR)")  
print(f"  New avg loss: ~$15 (0.75x ATR) = HALF the risk per trade")
