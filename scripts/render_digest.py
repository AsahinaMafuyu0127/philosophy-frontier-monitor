"""Preview the configured weekly report without changing persistent state."""

from __future__ import annotations

import sys

from philosophy_frontier_monitor.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["weekly-run", "--dry-run", *sys.argv[1:]]))
