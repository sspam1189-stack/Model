"""The dry-key alert must fire on the transition, not on every run.

Written 2026-09-16. alert_if_keys_exhausted was documented as "Email once
when every Odds API key has run dry" and then emailed on EVERY run. The keys
went dry on 2026-09-03 and the pipeline fired 16 more times, each one sending
the identical message. An alert that repeats twice a day becomes a filter
rule, which defeats the one thing it exists for -- catching a key dying
quietly, which is what cost the 2026 season its opening week.

Run:  cd pyNFL/scripts && python test_key_alert_throttle.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sources"))

from run_weekly import should_send_key_alert, KEY_ALERT_REPEAT_DAYS  # noqa: E402

NOW = datetime(2026, 9, 16, 20, 0, tzinfo=timezone.utc)

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def iso(dt):
    return dt.isoformat(timespec="seconds")


# 1. never alerted -> send (the transition into dry)
check(should_send_key_alert({}, NOW), "first dry run must alert")
check(should_send_key_alert(None, NOW), "missing state must alert")

# 2. alerted moments ago -> stay quiet. This is the 16 emails.
check(not should_send_key_alert({"lastAlertIso": iso(NOW - timedelta(minutes=30))}, NOW),
      "must not re-alert 30 minutes later")
check(not should_send_key_alert({"lastAlertIso": iso(NOW - timedelta(days=1))}, NOW),
      "must not re-alert the next day")
check(not should_send_key_alert({"lastAlertIso": iso(NOW - timedelta(days=6))}, NOW),
      "must not re-alert inside the repeat window")

# 3. still dry a week later -> remind. Silence forever is its own failure.
check(should_send_key_alert({"lastAlertIso": iso(NOW - timedelta(days=KEY_ALERT_REPEAT_DAYS))}, NOW),
      f"must remind after {KEY_ALERT_REPEAT_DAYS} days")
check(should_send_key_alert({"lastAlertIso": iso(NOW - timedelta(days=30))}, NOW),
      "must remind after a month")

# 4. junk state must not silence the alert -- failing open is the safe side
check(should_send_key_alert({"lastAlertIso": "not-a-date"}, NOW),
      "unparseable timestamp must alert rather than go quiet")
check(should_send_key_alert({"lastAlertIso": ""}, NOW),
      "empty timestamp must alert")

# 5. a naive timestamp (no tzinfo) must not blow up
check(not should_send_key_alert(
    {"lastAlertIso": (NOW - timedelta(hours=2)).replace(tzinfo=None).isoformat()}, NOW),
    "naive stored timestamps must still throttle")

if failures:
    print(f"FAILED ({len(failures)})")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print(f"PASSED  alerts on transition, quiet for {KEY_ALERT_REPEAT_DAYS}d, "
      f"reminds after, fails open on junk")
