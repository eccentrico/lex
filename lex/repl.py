"""Plain REPL: input() in, rich-rendered markdown out."""
import argparse
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from lex import agent, llm, memory, prompt, sessions
from lex.paths import lex_home

console = Console()
logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """File-only logging: console stays chat-only, but a failure (e.g. an
    LLM timeout) leaves a real traceback at ~/.lex/lex.log to debug from.

    force=True makes this take effect regardless of anything configured
    before it (some other harness code, or even a stale handler from a
    prior run) — the config that matters is the last one, always ours.
    basicConfig() with no force is a no-op once handlers exist, so a later
    services.kite_data import (which calls basicConfig(level=INFO) itself,
    with no filename, at import time) stays a harmless no-op after this.
    """
    logging.basicConfig(
        level=logging.INFO, filename=str(lex_home() / "lex.log"),
        filemode="a", format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True)

HELP = """Commands:
  /new           start a fresh session
  /resume [n]    resume the nth most recent session (default 1)
  /sessions      list recent sessions
  /memory        show memory file path + contents
  /research SYM  full multi-pass research on a symbol
                 --full  print every section, not just the answer
                 --quick skip the narrative pass (2 passes, faster)
  /reports [SYM] saved reports: symbols researched, or one symbol's history
                 -n N    print the Nth most recent report for SYM
  /quit          exit"""

# what the spinner says while each pass is running
_STAGES = {"facts": "gathering the record — filings, financials, ownership…",
           "narrative": "building the business picture and peer context…",
           "bear": "attacking the thesis…",
           "synthesis": "weighing the passes…"}


@dataclass
class State:
    session: Path
    messages: list = field(default_factory=list)


def handle_command(line: str, state: State):
    """Return reply text for /commands, None if `line` is a normal message."""
    if not line.startswith("/"):
        return None
    cmd, *rest = line.split()
    if cmd == "/new":
        state.session, state.messages = sessions.new_session(), []
        return "Started a fresh session."
    if cmd == "/resume":
        try:
            n = int(rest[0]) if rest else 1
        except ValueError:
            return f"'{rest[0]}' isn't a number — try /sessions to see the list."
        recent = sessions.list_sessions()
        if n < 1 or n > len(recent):
            return f"No session #{n}. {len(recent)} available — try /sessions."
        state.session = recent[n - 1]
        state.messages = [m for m in sessions.load(state.session)
                          if m.get("role") != "system"]
        return f"Resumed {state.session.name} ({len(state.messages)} messages)."
    if cmd == "/sessions":
        return "\n".join(f"{i}. {p.name}" for i, p in
                         enumerate(sessions.list_sessions(), 1)) or "No sessions yet."
    if cmd == "/memory":
        return f"{memory.memory_path()}\n\n{memory.read() or '(empty)'}"
    if cmd == "/research":
        return _research(rest)
    if cmd == "/reports":
        return _reports(rest)
    if cmd == "/quit":
        raise SystemExit(0)
    return HELP


def _research(rest: list):
    """/research SYM [--full] [--quick] — run the pipeline, print the report."""
    from lex import research, reports
    flags = {a for a in rest if a.startswith("-")}
    symbols = [a for a in rest if not a.startswith("-")]
    if not symbols:
        return "Usage: /research SYMBOL [--full] [--quick]"
    symbol = symbols[0].upper()
    depth = "brief" if "--quick" in flags else "full"
    mode = "full" if "--full" in flags else "brief"
    try:
        with console.status(_STAGES["facts"]) as status:
            report = research.run_and_save(
                symbol, depth=depth,
                progress=lambda stage: status.update(_STAGES.get(stage, stage)))
    except Exception as e:
        logger.exception("/research failed for %s", symbol)
        return f"[red]Research failed:[/] {e} — nothing was saved."
    tail = "" if mode == "full" else "\n\n*`/reports " + symbol + " -n 1 --full` for every section.*"
    return Markdown(reports.render(report, mode=mode) + tail)


def _reports(rest: list):
    """/reports — what's been researched; /reports SYM [-n N] [--full]."""
    from lex import reports
    flags = [a for a in rest if a.startswith("-")]
    words = [a for a in rest if not a.startswith("-")]
    if not words:
        symbols = reports.researched_symbols()
        if not symbols:
            return "No saved reports yet — try /research SYMBOL."
        return "\n".join(f"{s} ({len(reports.history(s))})" for s in symbols)

    symbol = words[0].upper()
    numbers = [int(w) for w in words[1:] if w.isdigit()]
    n = numbers[0] if numbers else (1 if "-n" in flags else 0)
    if not n:
        history = reports.history(symbol)
        if not history:
            return f"No saved reports for {symbol}."
        return "\n".join(
            f"{h['n']}. {h['generated_at']} ({h['age']}) — "
            f"{h['stance'] or 'no verdict'} / {h['confidence'] or 'unknown'}"
            for h in history)

    report = reports.load(symbol, n)
    if report is None:
        return f"No saved report #{n} for {symbol}."
    return Markdown(reports.render(report,
                                   mode="full" if "--full" in flags else "brief"))


def main(argv=None):
    from dotenv import load_dotenv
    load_dotenv()
    configure_logging()
    ap = argparse.ArgumentParser(prog="lex")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args(argv)
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url
    model = args.model or llm.default_model()
    client = llm.make_client()
    from lex.tools import TOOLS
    state = State(session=sessions.new_session())
    console.print(f"[bold]Lex[/] — {model}. /help for commands.")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        reply = handle_command(line, state)
        if reply is not None:
            console.print(reply)
            continue
        system = {"role": "system",
                  "content": prompt.build_system_prompt(memory.read())}
        state.messages.append({"role": "user", "content": line})
        buf = []
        try:
            with Live(Markdown("*thinking…*"), console=console,
                     refresh_per_second=12) as live:
                def on_token(t, buf=buf, live=live):
                    buf.append(t)
                    live.update(Markdown("".join(buf)))
                text = agent.run(client, model, [system] + state.messages, TOOLS,
                                 on_token=on_token)
        except Exception as e:
            logger.exception("agent.run failed for line: %r", line)
            console.print(f"[red]LLM error:[/] {e} — turn discarded, try again.")
            state.messages.pop()
            continue
        # ponytail: agent.run mutates its own [system] + state.messages copy, so
        # tool-call/tool-result intermediates never touch state.messages or the
        # JSONL — only user + final assistant turns persist. Keeps resumed
        # sessions clean and small; persist intermediates later if debugging needs it.
        # Both turns are written to the JSONL together, only after a successful
        # reply — an LLM failure above must never leave a dangling user turn
        # with no answer in a resumed session.
        state.messages.append({"role": "assistant", "content": text})
        sessions.append(state.session, state.messages[-2])
        sessions.append(state.session, state.messages[-1])
