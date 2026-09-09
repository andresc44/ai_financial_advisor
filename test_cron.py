#!/usr/bin/env python3
import csv
from datetime import datetime, timezone, timedelta
import os
import sys

# 0 * * * * /home/andres_cervera_rozo/ai_financial_advisor/venv/bin/python /home/andres_cervera_rozo/ai_financial_advisor/test_cron.py >> /home/andres_cervera_rozo/ai_financial_advisor/cron.log 2>&1
def get_aest_timestamp():
    """Generates a formatted timestamp in Australian Eastern Standard Time (AEST, UTC+10)."""
    aest_tz = timezone(timedelta(hours=10))
    return datetime.now(aest_tz).strftime("%Y-%m-%d %H:%M:%S AEST")

def get_free_swap_mb():
    """Reads /proc/meminfo on Linux to get free swap space in MB."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("SwapFree:"):
                    kb = int(line.split()[1])
                    return f"{kb / 1024:.2f} MB"
    except Exception:
        return "N/A (Not Linux)"
    return "0.00 MB"

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_file = os.path.join(base_dir, "cron_test_results.csv")

    timestamp = get_aest_timestamp()
    free_swap = get_free_swap_mb()

    # 1. Output to stdout (Captured by cron.log)
    print(f"[{timestamp}] [CRON TEST SUCCESS] Free Swap: {free_swap} | Python: {sys.executable}")

    # 2. Append row to existing CSV (mode="a" ensures it appends rather than overwrites)
    file_exists = os.path.isfile(csv_file)
    with open(csv_file, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Timestamp (AEST)", "Free Swap Space", "Status"])
        writer.writerow([timestamp, free_swap, "SUCCESS"])

if __name__ == "__main__":
    main()