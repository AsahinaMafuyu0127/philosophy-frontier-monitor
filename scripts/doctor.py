"""Compatibility wrapper for ``pfm doctor``."""

from __future__ import annotations

import sys

from philosophy_frontier_monitor.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["doctor", *sys.argv[1:]]))
