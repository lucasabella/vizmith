import argparse
import threading
import webbrowser

import uvicorn
from dotenv import load_dotenv


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

    args = parser.parse_args()

    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(f"http://{args.host}:{args.port}",)).start()

    uvicorn.run("vizmith.api:app", host=args.host, port=args.port)
