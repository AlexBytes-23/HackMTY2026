"""CLI for the development-only estate generator.

    python -m dev.generate_estate --seed 101 --out-dir dev/estates/101

Writes ``estate.db`` and ``ground_truth.json`` into the given directory.  The
answer key it writes may only be read by the evaluation harness, never by
anything under ``src/``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dev.estate_generator import EstateSpec, dump_answer_key, generate_estate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m dev.generate_estate",
        description="Generate a synthetic estate.db and its answer key.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        required=True,
        help="Seed for the generator; the same seed reproduces the same bytes.",
    )
    parser.add_argument(
        "--out-dir",
        required=True,
        help="Directory that receives estate.db and ground_truth.json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_path = out_dir / "estate.db"
    key_path = out_dir / "ground_truth.json"

    answer_key = generate_estate(EstateSpec(seed=args.seed), db_path)
    dump_answer_key(answer_key, key_path)

    print(f"estate: {db_path}")
    print(f"answer key: {key_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
