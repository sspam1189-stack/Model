# scripts/update.py
# Alias for run_daily.py — update pipeline

from run_daily import main

if __name__ == "__main__":
    import sys
    try:
        main(subject_label="[PY Update]")
    except Exception as err:
        print(err, file=sys.stderr)
        sys.exit(1)
