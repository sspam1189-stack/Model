# scripts/recap_run_daily.py
# Daily NCAA picks pipeline (with Yesterday's Recap)
# RECAPrun_daily.mjs was identical to run_daily.mjs — same pipeline logic

from run_daily import main

if __name__ == "__main__":
    import sys
    try:
        main()
    except Exception as err:
        print(err, file=sys.stderr)
        sys.exit(1)
