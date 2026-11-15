#!/usr/bin/env python3
"""Print, as JSON, which Python project a folder (or file) belongs to.

    python3 -B detect.py [PATH] [--spec SPEC]

Same as `run_tests.py --detect PATH`: best matching spec (score = number of
the spec's exercise files present), project root (walks up from exN/, or
down into a child such as 01/), and the exercise(s) PATH points at.
Exit 0 when a project was found, 1 otherwise.
"""

import argparse
import json
import os
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_tests import detect  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", nargs="?", default=os.getcwd())
    parser.add_argument("--spec", default=None)
    args = parser.parse_args()
    result = detect(args.path, args.spec)
    print(json.dumps(result, indent=2))
    return 0 if result["spec"] else 1


if __name__ == "__main__":
    sys.exit(main())
