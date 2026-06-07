"""MCP (Model Context Protocol) client — manages stdio-based MCP servers."""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from config import setup_logging, MCP_TIMEOUT, VERSION

log = setup_logging()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_mcp_configs(cwd: str) -> dict[str, dict]:
    """Load mcpServers configs from ~/.gus/mcp.json then .gus/mcp.json (project wins)."""
    configs: dict[str, dict] = {}

    sources = [
        Path.home() / ".gus" / "mcp.json",
        Path(cwd) / ".gus" / "mcp.json",
    ]
    # Also check project root when cwd differs
    root_cfg = _PROJECT_ROOT / ".gus" / "mcp.json"
    if root_cfg not in sources:
        sources.append(root_cfg)

    for path in sources:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                configs.update(data.get("mcpServers", {}))
                log.info("mcp: loaded config from %s", path)
            except Exception as e:
                log.warning("mcp: failed to load %s: %s", path, e)

    return configs


class _MCPServer:
    """One stdio MCP server subprocess (JSON-RPC 2.0)."""

    def __init__(self, name: str, config: dict) -> None:
        self.name = name
        self._config = config
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 1
        self.tools: list[dict] = []
        self._stdout_q: "queue.Queue[str | None]" = queue.Queue()

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        cmd = self._config.get("command", "")
        args = self._config.get("args", [])
        env = {**os.environ, **self._config.get("env", {})}

        self._process = subprocess.Popen(
            [cmd, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
        log.info("mcp: started '%s' (pid=%d)", self.name, self._process.pid)

        # Drain stdout/stderr on daemon threads so a chatty or hung server can
        # never fill a pipe buffer (deadlock) or block a read forever.
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

        resp = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "clientInfo": {"name": "gus", "version": VERSION},
        })
        if resp is None:
            raise RuntimeError(f"MCP server '{self.name}' did not respond to initialize")

        # Send initialized notification (no id)
        self._notify("notifications/initialized", {})

        tools_resp = self._send("tools/list", {})
        self.tools = (tools_resp or {}).get("tools", [])
        log.info("mcp: '%s' offers %d tool(s)", self.name, len(self.tools))

    def stop(self) -> None:
        proc = self._process
        self._process = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    # ── tool call ──────────────────────────────────────────────────────────

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        resp = self._send("tools/call", {"name": tool_name, "arguments": arguments})
        if resp is None:
            return "Error: MCP server returned no response"

        content = resp.get("content", [])
        parts: list[str] = []
        for item in content:
            t = item.get("type")
            if t == "text":
                parts.append(item.get("text", ""))
            elif t == "image":
                parts.append(f"[image: {item.get('mimeType', 'unknown')}]")
            else:
                parts.append(json.dumps(item))

        result = "\n".join(parts) or "(empty response)"
        if resp.get("isError"):
            return f"Error: {result}"
        return result

    # ── pipe pumps ─────────────────────────────────────────────────────────

    def _pump_stdout(self) -> None:
        proc = self._process
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:           # blocks in this thread only
            self._stdout_q.put(line)
        self._stdout_q.put(None)           # EOF sentinel

    def _pump_stderr(self) -> None:
        proc = self._process
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            log.debug("mcp[%s] stderr: %s", self.name, line.rstrip())

    # ── JSON-RPC transport ─────────────────────────────────────────────────

    def _send(self, method: str, params: dict) -> dict | None:
        with self._lock:
            proc = self._process
            if proc is None or proc.poll() is not None:
                log.error("mcp: '%s' process is not running", self.name)
                return None

            msg_id = self._next_id
            self._next_id += 1
            request = json.dumps({
                "jsonrpc": "2.0",
                "id": msg_id,
                "method": method,
                "params": params,
            })
            try:
                proc.stdin.write(request + "\n")  # type: ignore[union-attr]
                proc.stdin.flush()                # type: ignore[union-attr]
            except OSError as e:
                log.error("mcp: %s/%s IO error: %s", self.name, method, e)
                return None

            # Read responses from the pump thread with an overall deadline so a
            # hung or dead server can never block us forever.
            deadline = time.monotonic() + MCP_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.error("mcp: %s/%s timed out after %.0fs", self.name, method, MCP_TIMEOUT)
                    return None
                try:
                    line = self._stdout_q.get(timeout=remaining)
                except queue.Empty:
                    log.error("mcp: %s/%s timed out after %.0fs", self.name, method, MCP_TIMEOUT)
                    return None
                if line is None:
                    log.error("mcp: '%s' stdout closed (process exited)", self.name)
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "id" not in data:
                    continue  # notification — ignore
                if data["id"] != msg_id:
                    continue  # response to a different/earlier call — drop
                if "error" in data:
                    log.error("mcp: %s/%s error: %s", self.name, method, data["error"])
                    return None
                return data.get("result") or {}

    def _notify(self, method: str, params: dict) -> None:
        proc = self._process
        if proc is None or proc.poll() is not None:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        try:
            proc.stdin.write(msg + "\n")  # type: ignore[union-attr]
            proc.stdin.flush()            # type: ignore[union-attr]
        except OSError:
            pass


class MCPManager:
    """Manages all configured MCP servers for a session."""

    def __init__(self, cwd: str) -> None:
        self._cwd = cwd
        self._servers: dict[str, _MCPServer] = {}
        self._configs = _load_mcp_configs(cwd)

    def start_all(self) -> int:
        """Start all configured servers. Returns count of successfully started servers."""
        for name, cfg in self._configs.items():
            server = _MCPServer(name, cfg)
            try:
                server.start()
                self._servers[name] = server
            except Exception as e:
                log.error("mcp: failed to start '%s': %s", name, e)
        return len(self._servers)

    def get_tool_schemas(self) -> list[dict]:
        """Return OpenAI-compatible function schemas for all MCP tools."""
        schemas: list[dict] = []
        for server_name, server in self._servers.items():
            for tool in server.tools:
                fn_name = f"mcp__{server_name}__{tool['name']}"
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": fn_name,
                        "description": f"[{server_name}] {tool.get('description', '')}",
                        "parameters": tool.get("inputSchema") or {
                            "type": "object",
                            "properties": {},
                        },
                    },
                })
        return schemas

    def call_tool(self, fn_name: str, arguments: dict) -> str | None:
        """
        Route an mcp__<server>__<tool> call.
        Returns None if fn_name is not an MCP tool (lets caller fall through).
        """
        if not fn_name.startswith("mcp__"):
            return None
        parts = fn_name.split("__", 2)
        if len(parts) != 3:
            return f"Error: malformed MCP tool name '{fn_name}'"
        _, server_name, tool_name = parts
        server = self._servers.get(server_name)
        if server is None:
            return f"Error: MCP server '{server_name}' is not running"
        return server.call_tool(tool_name, arguments)

    def stop_all(self) -> None:
        for server in self._servers.values():
            server.stop()
        self._servers.clear()

    def list_servers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": name,
                "tools": [t["name"] for t in srv.tools],
                "running": srv._process is not None and srv._process.poll() is None,
            }
            for name, srv in self._servers.items()
        ]

    @property
    def server_count(self) -> int:
        return len(self._servers)

    @property
    def tool_count(self) -> int:
        return sum(len(s.tools) for s in self._servers.values())
