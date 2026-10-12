#!/usr/bin/env python3
"""
dv_flow.py - YAML + Jinja2 driven design-verification flow runner.

This file only checks the Python version and the required packages, then
starts the flow. The code lives in the dvflow/ package next to this file
(see dvflow/__init__.py for a map of the modules).

Requirements: Python 3.8 or newer, PyYAML >= 5.1, Jinja2 >= 2.11
    pip install -r requirements.txt

Run `python dv_flow.py --help` for the options.
"""

import sys

# Written without f-strings so that an old Python prints this message instead of a SyntaxError.
if sys.version_info < (3, 8):
    sys.exit("dv_flow: Python 3.8 or newer is required (this is Python %d.%d)" % sys.version_info[:2])

try:
    import jinja2  # noqa: F401
    import yaml  # noqa: F401
except ImportError as exc:
    sys.exit(f"dv_flow: missing Python package ({exc.name}). Install with: pip install -r requirements.txt")

from dvflow.cli import main

if __name__ == "__main__":
    sys.exit(main())
