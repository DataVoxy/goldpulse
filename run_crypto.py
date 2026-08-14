"""
run_crypto.py — CryptoPulse runner
Runs BTC + ETH strategy. 24/7 market, checks every 15 min.

Usage:
  py run_crypto.py          # Run once
  py run_crypto.py --loop   # Run every 15 min
"""
import sys
import time
from core.strategy_crypto import strategy_cycle, CHECK_INTERVAL

if __name__ == "__main__":
    if "--loop" in sys.argv:
        print(f"CryptoPulse loop started (every {CHECK_INTERVAL}s)")
        while True:
            try:
                strategy_cycle()
            except Exception as e:
                print(f"Error: {e}")
            time.sleep(CHECK_INTERVAL)
    else:
        strategy_cycle()
