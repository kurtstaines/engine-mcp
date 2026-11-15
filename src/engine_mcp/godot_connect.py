#!/usr/bin/env python3
"""godot_connect -- check a Godot MCP endpoint and register it with your MCP clients.

A Godot MCP addon serves an HTTP endpoint from the running editor (default
http://127.0.0.1:8000/mcp). This tool answers three separate questions:
is the addon installed in the project, is the endpoint live, and which
clients are registered to use it. Then it can register them in one command.

  engine-mcp-godot --project path/to/godot_project doctor
  engine-mcp-godot verify
  engine-mcp-godot --clients claude,codex connect
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

DEFAULT_URL = "http://127.0.0.1:8000/mcp"
DEFAULT_ADDON = "godot_ai"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
GROK_CONFIG = Path.home() / ".grok" / "config.toml"
PROJECT_MCP = Path.cwd() / ".mcp.json"  # overridden by --mcp-json
ALL_CLIENTS = ("claude", "codex", "grok", "hermes", "project")


def dumps(value: Any) -> str:
    """Pretty-print a value as UTF-8 JSON."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def endpoint_parts(url: str) -> tuple[str, int]:
    """Split an http(s) URL into (host, port); the port must be explicit."""
    match = re.match(r"^https?://([^/:]+):(\d+)", url)
    if not match:
        raise ValueError(f"Godot MCP URL must include host and port: {url}")
    return match.group(1), int(match.group(2))


def is_live(url: str) -> bool:
    """True if something accepts TCP connections on the URL's host and port."""
    try:
        host, port = endpoint_parts(url)
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except (OSError, ValueError):
        return False


def verify_endpoint(url: str) -> dict[str, Any]:
    """Send an MCP `initialize` request and report whether the endpoint answers 200."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "engine-mcp-godot-connect", "version": "1.0"},
            },
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = response.read(65536).decode("utf-8", errors="replace")
            return {
                "ok": response.status == 200,
                "status": response.status,
                "response": payload[:4000],
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


def _upsert_toml(path: Path, name: str, url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    # drop the old table (and its sub-tables) up to the next table header, so
    # values containing "[" (arrays) are removed whole
    pattern = re.compile(rf"(?ms)^\[mcp_servers\.{re.escape(name)}(?:\.[^\]\n]+)?\]\n.*?(?=^\[|\Z)")
    text = pattern.sub("", text).rstrip()
    path.write_text(
        text + f'\n\n[mcp_servers.{name}]\nurl = "{url}"\nenabled = true\n', encoding="utf-8"
    )


def _upsert_project(name: str, url: str) -> None:
    data = {"mcpServers": {}}
    if PROJECT_MCP.is_file():
        data = json.loads(PROJECT_MCP.read_text(encoding="utf-8-sig"))
    data.setdefault("mcpServers", {})[name] = {"type": "http", "url": url}
    PROJECT_MCP.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _hermes_config() -> Path:
    local = Path.home() / "AppData" / "Local" / "hermes" / "config.yaml"
    return local if local.is_file() else Path.home() / ".hermes" / "config.yaml"


def _upsert_hermes(name: str, url: str) -> None:
    path = _hermes_config()
    if not path.is_file():
        raise FileNotFoundError(path)
    text = path.read_text(encoding="utf-8")
    block = f"  {name}:\n    url: {json.dumps(url)}\n    enabled: true\n    connect_timeout: 60.0\n"
    text = re.sub(rf"(?m)^  {re.escape(name)}:\n(?:    .*\n)*", "", text)
    if re.search(r"(?m)^mcp_servers:\s*$", text):
        text = re.sub(r"(?m)^(mcp_servers:\s*\n)", r"\1" + block, text, count=1)
    else:
        text = text.rstrip() + "\n\nmcp_servers:\n" + block
    path.write_text(text, encoding="utf-8")


def register_client(client: str, name: str, url: str, scope: str) -> dict[str, Any]:
    """Register the endpoint with one client; returns {"ok": bool, ...} and never raises."""
    try:
        if client == "claude":
            exe = shutil.which("claude")
            if not exe:
                raise FileNotFoundError("claude CLI")
            subprocess.run(
                [exe, "mcp", "remove", name, "--scope", scope], capture_output=True, timeout=30
            )
            result = subprocess.run(
                [exe, "mcp", "add", "--transport", "http", name, url, "--scope", scope],
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode:
                raise RuntimeError(
                    (result.stderr or result.stdout or "claude mcp add failed").strip()
                )
        elif client == "codex":
            exe = shutil.which("codex")
            if exe:
                subprocess.run([exe, "mcp", "remove", name], capture_output=True, timeout=30)
                result = subprocess.run(
                    [exe, "mcp", "add", name, "--url", url],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )
                if result.returncode:
                    raise RuntimeError(
                        (result.stderr or result.stdout or "codex mcp add failed").strip()
                    )
            _upsert_toml(CODEX_CONFIG, name, url)
        elif client == "grok":
            _upsert_toml(GROK_CONFIG, name, url)
        elif client == "hermes":
            _upsert_hermes(name, url)
        elif client == "project":
            _upsert_project(name, url)
        else:
            raise ValueError(f"unknown client: {client}")
        return {"ok": True, "client": client, "url": url, "restart_required": True}
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "client": client, "url": url, "error": str(exc)}


def registration_status(name: str) -> dict[str, Any]:
    """Report, per client, whether a server called `name` is registered."""
    result: dict[str, Any] = {}
    for label, path in (("codex", CODEX_CONFIG), ("grok", GROK_CONFIG)):
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        result[label] = f"[mcp_servers.{name}]" in text
    project = {}
    if PROJECT_MCP.is_file():
        try:
            project = json.loads(PROJECT_MCP.read_text(encoding="utf-8-sig")).get("mcpServers", {})
        except json.JSONDecodeError:
            project = {}
    result["project"] = name in project
    hermes = _hermes_config()
    result["hermes"] = hermes.is_file() and bool(
        re.search(rf"(?m)^  {re.escape(name)}:", hermes.read_text(encoding="utf-8"))
    )
    result["claude"] = "probe with `claude mcp list`"
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns a process exit code."""
    global PROJECT_MCP
    parser = argparse.ArgumentParser(description="Godot MCP multi-client connector")
    parser.add_argument(
        "--project", default=".", help="Godot project folder (contains project.godot)"
    )
    parser.add_argument("--addon", default=DEFAULT_ADDON, help="addon folder name under addons/")
    parser.add_argument(
        "--mcp-json", default="", help="project .mcp.json to update (default: ./.mcp.json)"
    )
    parser.add_argument("--name", default="godot-ai", help="server name to register")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--scope", choices=("local", "project", "user"), default="local")
    parser.add_argument("--clients", default="all", help="claude,codex,grok,hermes,project or all")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "doctor", "verify", "connect"):
        sub.add_parser(name)
    args = parser.parse_args(argv)
    if args.mcp_json:
        PROJECT_MCP = Path(args.mcp_json)
    project = Path(args.project).resolve()
    addon = project / "addons" / args.addon / "plugin.cfg"

    if args.command == "status":
        print(
            dumps(
                {
                    "url": args.url,
                    "live": is_live(args.url),
                    "registrations": registration_status(args.name),
                }
            )
        )
        return 0
    if args.command == "doctor":
        live = is_live(args.url)
        payload = {
            "ok": (project / "project.godot").is_file() and addon.is_file(),
            "project": str(project),
            "project_exists": (project / "project.godot").is_file(),
            "addon_installed": addon.is_file(),
            "endpoint": args.url,
            "live": live,
            "registrations": registration_status(args.name),
            "next": "Run verify, then restart MCP clients after registration"
            if live
            else "Open Godot with the MCP addon enabled",
        }
        print(dumps(payload))
        return 0 if payload["ok"] else 1
    if args.command == "verify":
        result = verify_endpoint(args.url)
        print(dumps(result))
        return 0 if result.get("ok") else 1
    if args.command == "connect":
        clients = (
            list(ALL_CLIENTS)
            if args.clients == "all"
            else [c.strip() for c in args.clients.split(",") if c.strip()]
        )
        results = [register_client(c, args.name, args.url, args.scope) for c in clients]
        print(dumps({"ok": all(r["ok"] for r in results), "results": results}))
        return 0 if all(r["ok"] for r in results) else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
