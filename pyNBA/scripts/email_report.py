# scripts/email_report.py  --  thin wrapper around core/email_report.py
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

# Re-export public API
from core.email_report import send_email
