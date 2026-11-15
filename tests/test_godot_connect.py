"""Tests for the Godot connector. A local HTTP server stands in for the Godot addon."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from engine_mcp import godot_connect as gc


@pytest.fixture
def endpoint():
    """A fake MCP endpoint that answers `initialize` with 200."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()
    server.server_close()


def test_endpoint_parts():
    assert gc.endpoint_parts("http://127.0.0.1:8000/mcp") == ("127.0.0.1", 8000)
    with pytest.raises(ValueError):
        gc.endpoint_parts("http://localhost/mcp")


def test_live_endpoint_is_detected_and_verified(endpoint: str):
    assert gc.is_live(endpoint) is True
    result = gc.verify_endpoint(endpoint)
    assert result["ok"] is True and result["status"] == 200


def test_dead_endpoint_reports_not_ok(endpoint: str):
    host, _ = gc.endpoint_parts(endpoint)
    dead = f"http://{host}:1/mcp"  # port 1: nothing listens there
    assert gc.is_live(dead) is False
    assert gc.verify_endpoint(dead)["ok"] is False
    assert gc.is_live("not a url") is False


def test_upsert_toml_replaces_existing_entry_and_keeps_others(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "x"\n\n'
        '[mcp_servers.godot-ai]\ncommand = "old"\nargs = ["a", "b"]\n\n'
        '[mcp_servers.godot-ai.env]\nKEY = "v"\n\n'
        '[mcp_servers.other]\nurl = "http://keep"\n',
        encoding="utf-8",
    )
    gc._upsert_toml(config, "godot-ai", "http://new")
    gc._upsert_toml(config, "godot-ai", "http://new")  # idempotent
    text = config.read_text(encoding="utf-8")
    assert text.count("[mcp_servers.godot-ai]") == 1
    assert "old" not in text and '"a", "b"' not in text and "KEY" not in text
    assert 'url = "http://new"' in text
    assert 'model = "x"' in text and 'url = "http://keep"' in text


def test_project_registration_and_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mcp_json = tmp_path / ".mcp.json"
    mcp_json.write_text('{"mcpServers": {"other": {"command": "x"}}}', encoding="utf-8")
    monkeypatch.setattr(gc, "PROJECT_MCP", mcp_json)
    monkeypatch.setattr(gc, "CODEX_CONFIG", tmp_path / "codex.toml")
    monkeypatch.setattr(gc, "GROK_CONFIG", tmp_path / "grok.toml")
    monkeypatch.setattr(gc, "_hermes_config", lambda: tmp_path / "hermes.yaml")

    assert gc.registration_status("godot-ai")["project"] is False
    assert gc.register_client("project", "godot-ai", "http://u", "local")["ok"] is True
    assert gc.register_client("grok", "godot-ai", "http://u", "local")["ok"] is True
    servers = json.loads(mcp_json.read_text(encoding="utf-8"))["mcpServers"]
    assert servers["godot-ai"] == {"type": "http", "url": "http://u"} and "other" in servers
    status = gc.registration_status("godot-ai")
    assert status["project"] is True and status["grok"] is True and status["codex"] is False


def test_hermes_upsert_inserts_under_existing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = tmp_path / "config.yaml"
    config.write_text("model: x\nmcp_servers:\n  other:\n    url: keep\n", encoding="utf-8")
    monkeypatch.setattr(gc, "_hermes_config", lambda: config)
    gc._upsert_hermes("godot-ai", "http://u")
    gc._upsert_hermes("godot-ai", "http://u")  # idempotent
    text = config.read_text(encoding="utf-8")
    assert text.count("  godot-ai:") == 1 and text.count("mcp_servers:") == 1
    assert "  other:\n    url: keep" in text


def test_register_client_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gc, "_hermes_config", lambda: tmp_path / "missing.yaml")
    assert gc.register_client("hermes", "n", "http://u", "local")["ok"] is False
    assert gc.register_client("nope", "n", "http://u", "local")["ok"] is False


def test_doctor_cli(tmp_path: Path, capsys: pytest.CaptureFixture, endpoint: str):
    project = tmp_path / "game"
    (project / "addons" / "godot_ai").mkdir(parents=True)
    mcp_json = str(tmp_path / ".mcp.json")
    base = ["--project", str(project), "--url", endpoint, "--mcp-json", mcp_json]
    assert gc.main([*base, "doctor"]) == 1  # no project.godot, no plugin.cfg yet
    (project / "project.godot").touch()
    (project / "addons" / "godot_ai" / "plugin.cfg").touch()
    capsys.readouterr()
    assert gc.main([*base, "doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["addon_installed"] and report["live"] and report["project_exists"]
    assert gc.main([*base, "verify"]) == 0
    assert gc.main([*base, "--clients", "project", "connect"]) == 0
