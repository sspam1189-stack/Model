# QA Smoke Tests

Run from repo root:

```
python scripts/qa/verify_python_engines.py
node scripts/qa/verify_js_engines.mjs
```

What they verify:
- NBA / Fullseason include totals picks.
- NCAA totals are disabled.
- Fullseason includes H2H fields; NBA does not.
