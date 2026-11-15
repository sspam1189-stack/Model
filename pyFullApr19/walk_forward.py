#!/usr/bin/env python3
# pyFullApr19/walk_forward.py
#
# Walk the 2026-04-19 Python full-season model (pyFullApr19/pyFull/scripts and
# pyFullApr19/core, verbatim copies of that day's code) forward on this season's
# saved inputs. Testing only. The driver is shared with the March copy; see
# pyFullMar19/walk_forward.py for how it works.
#
#   python pyFullApr19/walk_forward.py --from 20261023 --to 20261112
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pyFullMar19"))
import walk_forward

if __name__ == "__main__":
    walk_forward.main("apr19")
