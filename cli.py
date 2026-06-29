"""Command-line entry point for weread-shelf.

Usage:
    python cli.py pull              # one-off pull of all data into SQLite
    python cli.py probe <api_name>  # raw gateway call, pretty-printed (debugging)
    python cli.py serve             # run the web server + scheduler
"""

from __future__ import annotations

import argparse
import json
import logging
import sys


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_pull(args: argparse.Namespace) -> int:
    from app.fetcher import run_daily_pull

    counts = run_daily_pull(kind="manual")
    print(json.dumps(counts, ensure_ascii=False, indent=2))
    return 0


def cmd_probe(args: argparse.Namespace) -> int:
    from app.client import WeReadClient

    params = {}
    for kv in args.param or []:
        key, _, value = kv.partition("=")
        if value.isdigit():
            value = int(value)
        params[key] = value
    with WeReadClient() as client:
        data = client.call(args.api_name, **params)
    print(json.dumps(data, ensure_ascii=False, indent=2)[: args.limit])
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from app.config import get_settings

    uvicorn.run("app.web:app", host="0.0.0.0", port=get_settings().port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="weread-shelf")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("pull", help="one-off pull of all data").set_defaults(func=cmd_pull)

    pr = sub.add_parser("probe", help="raw gateway call (debugging)")
    pr.add_argument("api_name")
    pr.add_argument("-p", "--param", action="append", help="key=value (repeatable)")
    pr.add_argument("--limit", type=int, default=6000)
    pr.set_defaults(func=cmd_probe)

    sub.add_parser("serve", help="run web server + scheduler").set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
