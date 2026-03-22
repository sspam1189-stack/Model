# scripts/calibration.py  --  thin wrapper around core/calibration.py
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Re-export public API
from core.calibration import build_calibration_table, build_calibration_html, BUCKETS, JUICE
