#!/usr/bin/env python3
# scripts/recap_run_daily.py

import sys

from dotenv import load_dotenv
load_dotenv()

from run_daily import main

if __name__ == "__main__":
    try:
        main(subject_label="[PY Recap]")
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
