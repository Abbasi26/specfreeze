"""Command-line interface for specforge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from specforge import __version__
from specforge.demo import run_demo
from specforge.errors import SpecforgeError
from specforge.pipeline import (
    ask_stage,
    build_stage,
    fix_stage,
    make_live_session,
    run_all,
    spec_stage,
)
from specforge.resources import read_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="specforge",
        description=(
            "Decision-driven code generation. Closed questions are answered and "
            "frozen into a contract before a code model writes anything."
        ),
    )
    parser.add_argument("--version", action="version", version=f"specforge {__version__}")
    parser.add_argument("--quiet", action="store_true", help="suppress progress lines")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--quiet", action="store_true", help="suppress progress lines")
    sub = parser.add_subparsers(dest="command", required=True)

    ask = sub.add_parser("ask", parents=[shared], help="answer the question catalog and write answers")
    _add_out(ask)
    ask.add_argument("--spec", required=True, type=Path, help="specification markdown")
    ask.add_argument("--catalog", type=Path, help="YAML catalog (default: the shipped general catalog)")
    _add_ask_flags(ask)
    ask.set_defaults(func=_cmd_ask)

    spec = sub.add_parser("spec", parents=[shared], help="distill answers and freeze the interface contract")
    _add_out(spec)
    spec.add_argument("--threshold", type=float, default=0.6, help="confidence below this is low (default 0.6)")
    spec.set_defaults(func=_cmd_spec)

    build = sub.add_parser("build", parents=[shared], help="generate code against the frozen contract")
    _add_out(build)
    build.add_argument("--write", type=Path, help="also copy the module to this path (prints a preview first)")
    build.set_defaults(func=_cmd_build)

    fix = sub.add_parser("fix", parents=[shared], help="repair failing tests without breaking the contract")
    _add_out(fix)
    fix.add_argument("--test-cmd", help="test command, split without a shell (default: pytest)")
    fix.add_argument("--rounds", type=int, default=4, help="repair rounds (default 4)")
    fix.add_argument("--threshold", type=float, default=0.6)
    fix.add_argument("--apply", action="store_true", help="replace the module with an accepted repair")
    fix.add_argument("--write", type=Path, help="with --apply, also copy the module to this path")
    fix.set_defaults(func=_cmd_fix)

    run = sub.add_parser("run", parents=[shared], help="ask, freeze, generate, and optionally repair")
    _add_out(run)
    run.add_argument("--spec", required=True, type=Path)
    run.add_argument("--catalog", type=Path)
    run.add_argument("--tests", type=Path, help="test file or directory to copy into the run")
    run.add_argument("--test-cmd")
    _add_ask_flags(run)
    run.add_argument("--fix-rounds", type=int, default=4)
    run.add_argument("--apply", action="store_true", help="keep an accepted repair (default is a preview)")
    run.add_argument("--write", type=Path, help="copy the resulting module to this path")
    run.set_defaults(func=_cmd_run)

    demo = sub.add_parser("demo", parents=[shared], help="offline end-to-end example (no API key, no network)")
    demo.add_argument("--out", type=Path, default=Path(".specforge/demo"), help="output directory")
    demo.set_defaults(func=_cmd_demo)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 2
    try:
        return args.func(args)
    except SpecforgeError as exc:
        print(f"specforge: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"specforge: {exc}", file=sys.stderr)
        return 2


def _add_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True, type=Path, help="run directory for artifacts")
    parser.add_argument("--force", action="store_true", help="redo this stage even if artifacts exist")


def _add_ask_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rounds", type=int, default=2, help="follow-up rounds after the base catalog (default 2)")
    parser.add_argument("--threshold", type=float, default=0.6, help="confidence below this is uncertain (default 0.6)")
    parser.add_argument("--max-questions", type=int, default=100, help="cap, at most 100 (default 100)")
    parser.add_argument(
        "--model-followups",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="ask the spec model for extra follow-ups when an answer is uncertain (default: on)",
    )


def _catalog(session, path: Path | None) -> Path:
    """Return a catalog path. The shipped catalog is copied into the run directory."""
    if path is not None:
        return path
    dest = session.run.path / "catalog.yaml"
    dest.write_text(read_text("data/catalogs/general.yaml"), encoding="utf-8")
    return dest


def _cmd_ask(args) -> int:
    session = make_live_session(args.out, force=args.force, quiet=args.quiet)
    session.say(f"Writing artifacts to {session.run.path}. No files outside this directory are changed.")
    ask_stage(
        session,
        args.spec,
        _catalog(session, args.catalog),
        rounds=args.rounds,
        threshold=args.threshold,
        max_questions=args.max_questions,
        model_followups=args.model_followups,
        force=args.force,
    )
    return 0


def _cmd_spec(args) -> int:
    session = make_live_session(args.out, force=args.force, quiet=args.quiet)
    session.say(f"Writing artifacts to {session.run.path}. No files outside this directory are changed.")
    spec_stage(session, threshold=args.threshold, force=args.force)
    return 0


def _cmd_build(args) -> int:
    session = make_live_session(args.out, force=args.force, quiet=args.quiet)
    session.say(f"Writing artifacts to {session.run.path}.")
    build_stage(session, force=args.force, write_path=args.write)
    return 0


def _cmd_fix(args) -> int:
    session = make_live_session(args.out, force=args.force, quiet=args.quiet)
    if not args.apply:
        session.say("Preview only. Pass --apply to replace the module after a repair that does not weaken the tests.")
    return fix_stage(
        session,
        test_cmd=args.test_cmd,
        rounds=args.rounds,
        apply=args.apply,
        threshold=args.threshold,
        write_path=args.write,
    )


def _cmd_run(args) -> int:
    session = make_live_session(args.out, force=args.force, quiet=args.quiet)
    session.say(f"Writing artifacts to {session.run.path}.")
    if not args.apply:
        session.say("Repairs stay in preview/ unless you pass --apply.")
    return run_all(
        session,
        spec_path=args.spec,
        catalog_path=_catalog(session, args.catalog),
        tests_path=args.tests,
        rounds=args.rounds,
        fix_rounds=args.fix_rounds,
        threshold=args.threshold,
        max_questions=args.max_questions,
        model_followups=args.model_followups,
        force=args.force,
        apply=args.apply,
        test_cmd=args.test_cmd,
        write_path=args.write,
    )


def _cmd_demo(args) -> int:
    return run_demo(args.out, apply=True, quiet=args.quiet)
