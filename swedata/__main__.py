"""Command line: python -m swedata {fetch,extract,build,all}"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .extract import DEFAULT_MODEL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="swedata", description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"), help="screenshots, one folder per highlight")
    parser.add_argument("--extracted-dir", type=Path, default=Path("data/extracted"), help="per-slide JSON cache")
    parser.add_argument("--out-dir", type=Path, default=Path("output"))
    sub = parser.add_subparsers(dest="command", required=True)

    def add_fetch_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--login", required=True, help="your Instagram username (highlights need a logged-in account)")
        p.add_argument("--profile", default="zero2sudo", help="account whose highlights to download")
        p.add_argument("--session-file", help="instaloader session file (default: instaloader's own location)")
        p.add_argument("--only", nargs="*", help="only these highlight titles")

    def add_extract_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--model", default=DEFAULT_MODEL)
        p.add_argument("--effort", default="medium", choices=["low", "medium", "high"], help="reasoning effort")
        p.add_argument("--workers", type=int, default=4, help="highlights processed in parallel")
        p.add_argument("--force", action="store_true", help="re-extract slides that are already cached")

    add_fetch_args(sub.add_parser("fetch", help="download highlight screenshots from Instagram"))
    add_extract_args(sub.add_parser("extract", help="OCR + structure screenshots with a vision model (OpenRouter)"))
    sub.add_parser("build", help="generate the searchable site, doc, CSV and JSON")
    run_all = sub.add_parser("all", help="fetch, extract and build")
    add_fetch_args(run_all)
    add_extract_args(run_all)

    args = parser.parse_args(argv)

    if args.command in ("fetch", "all"):
        from .fetch import fetch_highlights

        fetch_highlights(args.login, args.profile, args.raw_dir, args.session_file, args.only)

    failures = 0
    if args.command in ("extract", "all"):
        from .extract import extract_all

        failures = extract_all(args.raw_dir, args.extracted_dir, args.model, args.effort, args.workers, args.force)

    if args.command in ("build", "all", "extract"):
        from .build import build

        build(args.extracted_dir, args.out_dir)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
