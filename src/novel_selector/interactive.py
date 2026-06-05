from __future__ import annotations

import argparse
import shlex
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from .config import Settings
from .db import Database
from .diagnostics import status_text


@dataclass(frozen=True)
class InteractiveCommand:
    name: str
    description: str


def run_interactive_shell(
    settings: Settings,
    parser: argparse.ArgumentParser,
    command_help: list[InteractiveCommand],
    execute: Callable[[argparse.Namespace], int],
) -> int:
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import HTML
    except ImportError:
        print("Interactive mode requires prompt_toolkit. Run `uv sync` and try again.")
        return 1

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if not text.startswith("/"):
                return
            current = text.split()[0]
            for item in command_help:
                token = "/" + item.name
                if token.startswith(current):
                    yield Completion(
                        token,
                        start_position=-len(current),
                        display=HTML(f"<b>{token}</b>    <ansigray>{item.description}</ansigray>"),
                    )

    db = Database(settings.db_path)
    print("Novel Selector interactive mode")
    print(status_text(settings, db))
    print("Type / to choose a command, /help for help, /exit to quit.")
    try:
        session = PromptSession(completer=SlashCompleter(), complete_while_typing=True, mouse_support=True)
    except Exception:
        print("Interactive terminal UI unavailable; falling back to plain input.")
        return run_plain_shell(parser, command_help, execute)
    while True:
        try:
            line = session.prompt("novel-selector> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        result = handle_interactive_line(line, parser, command_help, execute)
        if result is not None:
            return result


def run_plain_shell(
    parser: argparse.ArgumentParser,
    command_help: list[InteractiveCommand],
    execute: Callable[[argparse.Namespace], int],
) -> int:
    while True:
        try:
            line = input("novel-selector> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        result = handle_interactive_line(line, parser, command_help, execute)
        if result is not None:
            return result


def handle_interactive_line(
    line: str,
    parser: argparse.ArgumentParser,
    command_help: list[InteractiveCommand],
    execute: Callable[[argparse.Namespace], int],
) -> int | None:
    if not line:
        return None
    if line in {"/exit", "/quit", "exit", "quit"}:
        return 0
    if line in {"/help", "help", "/"}:
        print_interactive_help(command_help)
        return None
    if line.startswith("/"):
        line = line[1:]
    try:
        args = parser.parse_args(shlex.split(line))
    except SystemExit:
        return None
    execute(args)
    return None


def print_interactive_help(command_help: list[InteractiveCommand]) -> None:
    lines = ["Commands:"]
    lines.extend(f"  /{item.name:<13} {item.description}" for item in command_help)
    page_size = max(4, shutil.get_terminal_size((80, 12)).lines - 4)
    for start in range(0, len(lines), page_size):
        page = lines[start : start + page_size]
        print("\n".join(page))
        if start + page_size >= len(lines):
            break
        print("-- more -- Press Enter to continue, q to stop: ", end="")
        answer = input().strip().lower()
        if answer == "q":
            break
