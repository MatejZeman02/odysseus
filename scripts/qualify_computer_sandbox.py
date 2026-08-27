#!/usr/bin/env python3
"""Print or run the fixed Computer Help containment qualification.

This is an operator aid, not a command runner.  It accepts no image, mount,
or command arguments: the reviewed digest-pinned image must already be set in
``ODYSSEUS_COMPUTER_SANDBOX_IMAGE`` and present locally.  The resulting JSON
is intentionally the same safe readiness/qualification data exposed to the
browser, so it contains no host paths or container arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.computer_sandbox import SandboxQualificationError, qualify_containment, readiness


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="run the fixed hostile containment fixture after reviewing and configuring the image",
    )
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({"readiness": readiness().public_payload()}, sort_keys=True))
        return 0
    try:
        result = qualify_containment()
    except SandboxQualificationError as exc:
        print(json.dumps({"qualified": False, "detail": str(exc), "readiness": readiness().public_payload()}, sort_keys=True))
        return 2
    print(json.dumps({"qualified": True, "result": result, "readiness": readiness().public_payload()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
