"""The designer's tools for outside agents: an MCP server on stdio over one project file.

Run with ``python -m pixelheart_designer.mcp --project <character.json or its folder>``.
Every call loads the character from disk, runs one of the designer's tools and, when
the tool changed something, saves the result through the core's atomic save. This is
the one module in the package that imports the ``mcp`` SDK and the one that writes
files; nothing else in the package imports it.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from pixelheart_core.projects import ProjectError, load_project, project_path, save_project

from .tools import ToolContext, ToolError, run_tool, tool_specs


SERVER_NAME = "pixelheart-designer"
SERVER_VERSION = "0.1.0"   # Pixelheart's own version; this package must not import ``pixelheart`` to read it.
CLOSE_WARNING = ("Close this character in Pixelheart while another agent edits it: the app saves automatically "
                 "a moment after each edit and would overwrite the changes made here.")
UNEXPECTED = "The tool failed unexpectedly ({kind}). Nothing was changed."
INSTRUCTIONS = """These tools design the text side of one Stardew Valley villager in a Pixelheart project: who they are, what they say, their gifts, their daily routine and their heart events. Every call edits that character's file directly and is saved at once. There is no undo here, so read first and write deliberately.

Start with read_character. Confirm item ids with search_items and place ids with list_places before using them. Write like a villager, not a form: short, specific lines with a distinct voice. Dialogue syntax: end a sentence with $h (happy), $s (sad), $l (love), $a (angry) or $u (unique) to change their portrait, or nothing for neutral; #$b# starts a new box; @ is the farmer's name. Use nothing else. A character needs an Introduction line and one line per weekday to have a voice, gifts need at least one loved item, and heart events sit at 2, 4, 6, 8, 10 and 14 hearts, 14 only when they can be romanced. These tools are the whole set: anything without one here, such as portraits, sprites, homes and exports, is made by the creator in the app."""


def open_project(path) -> tuple[Path, dict]:
    """The resolved character.json and its document, or a ProjectError worded for the terminal."""
    file = project_path(path)
    if not file.is_file():
        raise ProjectError(f"No Pixelheart project at {file}.")
    return file, load_project(file)


def banner(file: Path, document: dict) -> str:
    character = document.get("character") if isinstance(document.get("character"), dict) else {}
    name = str(character.get("name") or "").strip() or "this character"
    return f"Pixelheart AI designer · MCP on stdio · {name} · {file}\n{CLOSE_WARNING}"


def text_result(text: str, *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=is_error)


def call_tool(file, name: str, arguments: dict | None, client_label: str) -> types.CallToolResult:
    """One call over the file: load, run the tool, save when it changed something, answer with its message."""
    try:
        document = load_project(file)
        outcome = run_tool(name, document, {} if arguments is None else arguments, ToolContext(client_label))
        if outcome.receipt is not None:
            save_project(outcome.document, file)
    except (ToolError, ProjectError) as exc:
        return text_result(str(exc), is_error=True)
    except Exception as exc:  # a tool bug must not take the server down with it
        return text_result(UNEXPECTED.format(kind=type(exc).__name__), is_error=True)
    return text_result(outcome.message)


def client_label(ctx) -> str:
    """"mcp:<client name>" from the handshake, or plain "mcp" when the client did not say."""
    params = getattr(getattr(ctx, "session", None), "client_params", None)
    info = getattr(params, "client_info", None)
    name = " ".join(str(getattr(info, "name", "") or "").split())[:60]
    return f"mcp:{name}" if name else "mcp"


def build_server(file: Path) -> Server:
    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(name=spec.name, description=spec.description, input_schema=spec.input_schema)
                                            for spec in tool_specs()])

    async def on_call_tool(ctx, params):
        return call_tool(file, params.name, params.arguments, client_label(ctx))

    return Server(SERVER_NAME, version=SERVER_VERSION, instructions=INSTRUCTIONS,
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve(file: Path) -> None:
    server = build_server(file)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pixelheart_designer.mcp",
        description="Serve the AI designer's tools to an MCP client over stdio, editing one Pixelheart character. "
                    "Close that character in Pixelheart first; the app saves automatically and would overwrite the agent's changes.")
    parser.add_argument("--project", required=True, metavar="PATH",
                        help="A character.json file, or the project folder that holds it.")
    args = parser.parse_args(argv)
    try:
        file, document = open_project(args.project)
    except ProjectError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(banner(file, document), file=sys.stderr, flush=True)
    asyncio.run(serve(file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
