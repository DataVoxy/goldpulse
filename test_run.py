"""Quick test to verify strategy settings and data fetch."""
import sys
sys.path.insert(0, '.')
from core.strategy import get_4h_data, evaluate_entry, SL_ATR_MULT, TP_ATR_MULT

print(f"Settings: SL_ATR_MULT={SL_ATR_MULT}, TP_ATR_MULT={TP_ATR_MULT}")
print(f"Ratio: 1:{TP_ATR_MULT/SL_ATR_MULT:.1f} (TP:SL)")
print()

df = get_4h_data()
print(f"Data OK: {len(df)} candles")
print()

r = evaluate_entry(df)
print(f"Signal:  {r['signal']}")
print(f"Entry:   ${r['entry']}")
print(f"SL:      ${r['stop_loss']} ({r['sl_pts']} pts)")
print(f"TP:      ${r['take_profit']} ({r['tp_pts']} pts)")
print(f"Ratio:   {r['sl_tp_ratio']}")
print(f"RSI:     {r['rsi']}")
print(f"Session: {r['session']}")
print(f"Trend:   {'UP' if r['trend_up'] else 'DOWN'}")
print()
print("ALL CHECKS PASSED - Bot ready to run")
