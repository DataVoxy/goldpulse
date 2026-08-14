"""
Update all dashboard data and upload to R2.
Run this via Task Scheduler every 30 minutes.

Usage:
  py update_all_dashboards.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

scripts = [
    ROOT / "landing" / "generate_dashboard_data.py",
    ROOT / "landing" / "generate_silver_dashboard_data.py",
    ROOT / "landing" / "generate_crypto_dashboard_data.py",
]

print("=== Updating all dashboards ===")

for script in scripts:
    print(f"  Running {script.name}...")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    if result.stdout:
        print(f"    {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"    ERROR: {result.stderr[:100]}")

# Upload to R2
print("  Uploading to R2...")
result = subprocess.run(
    [sys.executable, str(ROOT / "deploy" / "upload_to_r2.py")],
    capture_output=True, text=True, timeout=30
)
if result.stdout:
    for line in result.stdout.strip().split("\n"):
        print(f"    {line}")

print("=== Done ===")
