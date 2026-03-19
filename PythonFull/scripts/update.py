#!/usr/bin/env python3
# scripts/update.py
# Daily NBA picks pipeline (update variant — no dotenv, minor label differences)
# Same pipeline as run_daily.py with minor cosmetic differences in HTML labels.
# The core logic, pipeline steps, and order are identical.

import sys

# update.mjs does NOT use dotenv (no require("dotenv").config())
# It is otherwise identical to run_daily.mjs except for minor label text
# ("Units" instead of "Flat", "edge" instead of "sDiff" in some HTML, etc.)

# Import everything from run_daily and re-use its main()
from run_daily import main

if __name__ == "__main__":
    try:
        main(subject_label="[PY] NBA Full Season Update")
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
