"""
run_silver.py — SilverPulse runner
Runs the silver strategy in a loop (same as GoldPulse but for silver).

Usage:
  py run_silver.py          # Run once
  py run_silver.py --loop   # Run every 30 min
"""
import sys
import time
from core.strategy_silver import strategy_cycle, CHECK_INTERVAL

if __name__ == "__main__":
    if "--loop" in sys.argv:
        print(f"SilverPulse loop started (every {CHECK_INTERVAL}s)")
        while True:
            try:
                strategy_cycle()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(CHECK_INTERVAL)
    else:
        strategy_cycle()
