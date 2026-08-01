import argparse
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

# The question set is a fixture rather than something the package ships: it asks about the
# synthetic dataset and about nothing else, so it only means anything in a checkout. A
# wheel that has no tests directory gets a message naming --questions rather than a stack.
QUESTIONS = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "evals" / "questions.json"


def main() -> None:
    # The command is what reads configuration off disk, not the application. Importing
    # vizmith should never pull in whatever .env happens to sit in the working directory,
    # and a real environment variable still wins over the file.
    load_dotenv()

    parser = argparse.ArgumentParser(prog="vizmith")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="Start the local server and open the browser")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--no-browser", action="store_true")

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

    if args.command == "eval":
        sys.exit(_eval(args))

    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(f"http://{args.host}:{args.port}",)).start()

    uvicorn.run("vizmith.api:app", host=args.host, port=args.port)


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
    from vizmith.api import constrains, model, profiles, source, state_dir

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
