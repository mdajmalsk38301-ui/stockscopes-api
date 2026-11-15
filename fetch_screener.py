"""
fetch_screener.py - runs on GitHub Actions (not on Render) and saves company numbers to data/screener.json.

Yahoo refuses the company-details calls that come from Render, but GitHub's machines are usually let through.
The API (screener.py) then reads the saved file from GitHub, so Render never has to ask Yahoo itself.
Run by hand with:  python fetch_screener.py
"""
import json
import os
import sys
import time
from datetime import datetime

os.environ["NO_WARM"] = "1"                  # do not start the scanner's background download just by importing it

import screener  # noqa: E402

OUT = os.path.join("data", "screener.json")


def main():
    syms = screener.universe()
    rows, misses = [], 0
    for i, sym in enumerate(syms):
        row = screener._one(sym)             # tries the company-details call, then the statements fallback, with waits
        if row:
            rows.append(row)
            misses = 0
        else:
            misses += 1
        print("%3d/%d %-12s %s" % (i + 1, len(syms), sym, "ok" if row else "none"), flush=True)
        if misses >= 15 and not rows:
            print("Yahoo is refusing every call from here too. Stopping.")
            return 1
        time.sleep(1.2)                      # be gentle: slower is better than being blocked
    if len(rows) < 10:
        print("Only %d rows. Not saving." % len(rows))
        return 1
    os.makedirs("data", exist_ok=True)
    data = {
        "generated_at": datetime.now(screener.IST).isoformat(),
        "universe": "Nifty 50 + Sensex + extra",
        "count": len(rows),
        "source": "github-actions",
        "rows": rows,
    }
    with open(OUT, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print("Saved %d rows to %s" % (len(rows), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
    
