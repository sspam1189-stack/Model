# scripts/update.py
# Alias for run_daily.py — update.mjs was identical to run_daily.mjs

from run_daily import main

if __name__ == "__main__":
    import sys
    try:
        main(subject_label="[PY] NCAA Update")
    except Exception as err:
        print(err, file=sys.stderr)
        sys.exit(1)
