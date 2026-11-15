#!/usr/bin/env python3
# scripts/update.py

import sys

from run_daily import main

if __name__ == "__main__":
    try:
        main(subject_label="[PY Update]")
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)
