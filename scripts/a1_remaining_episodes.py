"""Print frozen-schedule missing A1 result count without modifying artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--expected", type=int, required=True)
    args = parser.parse_args()
    if args.expected < 0:
        raise SystemExit("expected must be non-negative")
    if not args.results.exists():
        print(args.expected)
        return
    try:
        data = json.loads(args.results.read_text(encoding="utf-8"))
        rows = data["simulations"]
        keys = {(str(row["task_id"]), int(row["trial"]), int(row["seed"])) for row in rows}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"cannot read A1 results: {exc}") from exc
    if len(keys) != len(rows):
        raise SystemExit("duplicate serialized episode key")
    if len(keys) > args.expected:
        raise SystemExit("serialized episode count exceeds frozen expected count")
    print(args.expected - len(keys))


if __name__ == "__main__":
    main()
