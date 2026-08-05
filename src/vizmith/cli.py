import argparse
import getpass
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from vizmith import config

# The question set is a fixture rather than something the package ships: it asks about the
# synthetic dataset and about nothing else, so it only means anything in a checkout. A
# wheel that has no tests directory gets a message naming --questions rather than a stack.
QUESTIONS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "evals" / "questions.json"


def main() -> None:
    # The command is what reads configuration off disk, not the application. A real
    # environment variable wins over a .env in the working directory, which wins over the
    # file `vizmith configure` wrote. See config.py.
    config.load()

    parser = argparse.ArgumentParser(prog="vizmith")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Start the local server and open the browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-browser", action="store_true")

    settings = commands.add_parser(
        "configure",
        help="Set the source and the model endpoint, in a file only you can read",
    )
    settings.add_argument(
        "--show",
        action="store_true",
        help="Print where each setting comes from rather than asking for one. Never a key.",
    )
    for name, why in config.SETTINGS:
        settings.add_argument(f"--{name.removeprefix('VIZMITH_').lower().replace('_', '-')}", help=why)

    evaluate = commands.add_parser("eval", help="Score the question set against the configured model")
    evaluate.add_argument("--questions", type=Path, default=QUESTIONS)
    evaluate.add_argument(
        "--only",
        nargs="+",
        default=(),
        metavar="NAME",
        help="Run these questions only. A run is billed per question and the one worth "
        "repeating is the one that failed.",
    )
    evaluate.add_argument("--out", type=Path, default=Path("eval-runs"))
    evaluate.add_argument(
        "--no-cache",
        action="store_true",
        help="Ask again even where the same prompt has already been answered.",
    )

    args = parser.parse_args()

    if args.command == "configure":
        sys.exit(_configure(args))

    if args.command == "eval":
        sys.exit(_eval(args))

    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(f"http://{args.host}:{args.port}",)).start()

    uvicorn.run("vizmith.api:app", host=args.host, port=args.port)


def _configure(args) -> int:
    """Write the configuration file, by flag or by asking.

    This is the whole of what may write it. Nothing over HTTP does, so a request still
    cannot name a database and the model key still has no path into a browser, which is
    what the file kept in the first place. See ROADMAP.md.

    Asking is the path a person on their own machine takes, and it shows what is set
    without showing the key: pressing return keeps whatever is there. Flags are for
    everyone else, including a script, and they are what a session with no terminal has.
    """
    if args.show:
        print(config.config_path())
        for name, where in config.described():
            print(f"  {name}: {where}")
        return 0

    given = {
        name: getattr(args, name.removeprefix("VIZMITH_").lower())
        for name, _ in config.SETTINGS
        if getattr(args, name.removeprefix("VIZMITH_").lower()) is not None
    }
    if not given:
        if not sys.stdin.isatty():
            print(
                "nothing to set. Pass the values as flags, or run this where there is a "
                "terminal to ask in. `vizmith configure --help` lists them.",
                file=sys.stderr,
            )
            return 2
        given = _ask()

    path = config.write(given)
    print(f"written to {path}")
    return 0


def _ask() -> dict[str, str]:
    """One prompt per setting, showing what is already set and keeping it on an empty
    answer. The key is read without echoing it and is never shown back."""
    stored = config.read()
    answers = {}
    print(f"Setting up Vizmith. Values are kept in {config.config_path()}.\n")
    for name, why in config.SETTINGS:
        print(why)
        if name == config.SECRET:
            answer = getpass.getpass(f"  {name} [{'set' if name in stored else 'not set'}]: ")
        else:
            answer = input(f"  {name} [{stored.get(name, '')}]: ")
        if answer.strip() != "":
            answers[name] = answer.strip()
        print()
    return answers


def _eval(args) -> int:
    """The harness, over the configured source and the configured model.

    Both come from `api`, which is where a question asked from the interface gets them, so
    a score is produced by the path a person's question takes rather than by a second one
    that could drift from it. That includes the probe: an endpoint that honours a schema is
    asked with it here too, because asking a different way would score a different prompt.
    """
    # Imported here rather than at the top, so `vizmith serve` does not build the app twice
    # and a checkout with no question set still starts a server.
    from vizmith import evals
    from vizmith.api import constrains, model, profiles, source
    from vizmith.config import state_dir

    if not args.questions.is_file():
        print(f"no question set at {args.questions}; pass --questions", file=sys.stderr)
        return 2

    asked = evals.questions(args.questions)
    catalog = source()
    writer = model()
    cache = None if args.no_cache else evals.Cache(state_dir() / "evals.json")

    try:
        record = evals.run(
            asked,
            profiles(catalog),
            writer,
            catalog,
            cache=cache,
            only=args.only,
            constrained=constrains(writer),
        )
    except ValueError as failure:
        print(failure, file=sys.stderr)
        return 2

    for score in record.scores:
        reached = f"{len(score.passed)}/{len(evals.LAYERS)}"
        detail = "" if score.complete else f"  {score.failed}: {score.reason}"
        print(f"{reached}  {score.name}{detail}")

    totals = record.totals
    print(
        f"\n{totals['complete']}/{totals['questions']} complete, "
        + ", ".join(f"{totals[layer]} {layer}" for layer in evals.LAYERS)
        + f", {totals['asked']} asked"
    )
    print(f"written to {evals.write(record, args.out)}")
    return 0
