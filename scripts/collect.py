"""Run the configured end-to-end weekly monitoring pipeline."""

from __future__ import annotations

import sys

from philosophy_frontier_monitor.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["weekly-run", *sys.argv[1:]]))
