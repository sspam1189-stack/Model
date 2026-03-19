#!/usr/bin/env python3
# scripts/recap_run_daily.py
# Daily NBA picks pipeline (recap variant — uses dotenv, minor layout differences)
# Same pipeline as run_daily.py with minor cosmetic differences in HTML layout
# (e.g., picks and record in 2-column layout instead of full-width rows).
# The core logic, pipeline steps, and order are identical.

import sys

from dotenv import load_dotenv
load_dotenv()

# Import everything from run_daily and re-use its main()
from run_daily import main

if __name__ == "__main__":
    try:
        main(subject_label="[PY] NBA Full Season Recap")
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
