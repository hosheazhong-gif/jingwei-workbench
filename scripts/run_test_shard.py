from __future__ import annotations

import argparse
import sys
import unittest
from collections.abc import Iterator
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = PROJECT_ROOT / "tests"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def iter_test_cases(suite: unittest.TestSuite) -> Iterator[unittest.TestCase]:
    """Flatten a discovered suite while preserving unittest's stable order."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iter_test_cases(item)
        else:
            yield item


def build_shard(index: int, count: int) -> tuple[unittest.TestSuite, int]:
    if count < 1:
        raise ValueError("shard count must be at least 1")
    if index < 0 or index >= count:
        raise ValueError(f"shard index must be between 0 and {count - 1}")

    discovered = unittest.defaultTestLoader.discover(
        str(TEST_ROOT),
        pattern="test_*.py",
    )
    cases = list(iter_test_cases(discovered))
    selected = [case for position, case in enumerate(cases) if position % count == index]
    return unittest.TestSuite(selected), len(cases)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run one deterministic shard of the unittest suite."
    )
    parser.add_argument("--index", type=int, required=True, help="zero-based shard index")
    parser.add_argument("--count", type=int, required=True, help="total shard count")
    args = parser.parse_args()

    try:
        suite, total = build_shard(args.index, args.count)
    except ValueError as exc:
        parser.error(str(exc))

    selected = suite.countTestCases()
    print(
        f"CI test shard {args.index + 1}/{args.count}: "
        f"{selected} of {total} tests",
        flush=True,
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
