#!/usr/bin/env python3
"""agent-loop: The beating heart of clanker."""

import argparse
import json
import os
import subprocess
import sys
from typing import Callable, Iterable, Literal
from datetime import datetime, timezone
import time
from pathlib import Path

import socket
import httpx  # pip install httpx

# ─── Configuration ──────────────────────────────────────────
SKILLS_PATH = Path(os.environ.get("CLANKER_SKILLS_PATH", "/etc/clanker/skills"))
WORKSPACE = Path("/workspace")
PROVIDER_SOCKET = Path(os.environ.get("CLANKER_PROVIDER_SOCKET", "/mnt/provider/clanker/provider.sock"))
PROVIDER_MODE = os.environ.get("CLANKER_PROVIDER_MODE", "socket")  # "socket", "direct", "offline"


# ─── Tool Definitions (for the LLM's function-calling) ──────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file in the workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path from workspace root."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Execute a shell command in the workspace. Returns stdout and stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run."}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_codebase",
            "description": "Search the workspace with ripgrep. Returns matching lines.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "file_pattern": {"type": "string", "description": "Optional glob (e.g., '*.py')"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory relative to workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {"type": "string", "default": "."}
                },
                "required": [],
            },
        },
    },
]




# ─── Tool Execution (sandboxed) ──────────────────────────────
def execute_tool(name: str, args: dict) -> str:
    """Execute a tool and return the result string."""
    if name == "read_file":
        filepath = (WORKSPACE / args["path"]).resolve()
        # Safety: ensure path stays inside workspace
        if not str(filepath).startswith(str(WORKSPACE.resolve())):
            return "Error: path escapes workspace"
        try:
            content = filepath.read_text()
            # Truncate large files to avoid bloating context
            if len(content) > 10_000:
                content = content[:10_000] + "\n... [truncated]"
            return content
        except Exception as e:
            return f"Error reading file: {e}"
    elif name == "run_shell":
        # Restrict available commands? At minimum, run inside workspace.
        try:
            result = subprocess.run(
                args["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(WORKSPACE),
            )
            output = result.stdout + result.stderr
            return output[:8000]  # truncate
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as e:
            return f"Error: {e}"
    elif name == "search_codebase":
        pattern = args["pattern"]
        file_glob = args.get("file_pattern", "")
        cmd = ["rg", "--line-number", "--max-count=50"]
        if file_glob:
            cmd.extend(["--glob", file_glob])
        cmd.append(pattern)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=15, cwd=str(WORKSPACE)
            )
            return result.stdout[:8000] or "No matches found."
        except subprocess.TimeoutExpired:
            return "Search timed out."
        except Exception as e:
            return f"Error: {e}"
    elif name == "list_files":
        directory = args.get("directory", ".")
        target = (WORKSPACE / directory).resolve()
        if not str(target).startswith(str(WORKSPACE.resolve())):
            return "Error: path escapes workspace"
        try:
            files = [str(p.relative_to(WORKSPACE)) for p in target.iterdir() if not p.name.startswith(".")]
            return "\n".join(files[:200])
        except Exception as e:
            return f"Error: {e}"
    else:
        return f"Unknown tool: {name}"


# ─── LLM Client (provider-agnostic) ──────────────────────────
def get_client():
    mode = os.environ.get("CLANKER_PROVIDER_MODE", "socket").lower()

    if mode == "tcp":
        # TCP mode: proxy runs on host, container reaches it via host.docker.internal
        endpoint = os.environ.get(
            "CLANKER_PROVIDER_ENDPOINT",
            "http://host.docker.internal:11434",
        )
        print(f"Provider mode: tcp, endpoint: {endpoint}", file=sys.stderr)
        client = httpx.Client(base_url=endpoint, timeout=60)

    elif mode == "direct":
        # Direct API access (no proxy), expects API key inside container
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY not set for direct mode")
        endpoint = os.environ.get(
            "CLANKER_PROVIDER_ENDPOINT",
            "https://api.deepseek.com/v1",
        )
        print(f"Provider mode: direct, endpoint: {endpoint}", file=sys.stderr)
        client = httpx.Client(
            base_url=endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    elif mode == "socket":
        # Unix socket mode: proxy on host, socket mounted into container
        socket_path = Path(
            os.environ.get("CLANKER_PROVIDER_SOCKET", "/var/run/provider.sock")
        )
        print(f"Provider mode: socket, path: {socket_path}", file=sys.stderr)
        if not socket_path.exists():
            raise RuntimeError(f"Socket not found at {socket_path}")
        transport = httpx.HTTPTransport(uds=str(socket_path), retries=0)
        client = httpx.Client(
            transport=transport,
            base_url="http://localhost",
            timeout=60,
        )
        # Note: endpoint is the socket proxy's base; no need for host.docker.internal

    else:
        raise RuntimeError(f"Unknown provider mode: {mode}")

    # Optional connectivity test (remove if you don't want to waste tokens)
    try:
        response = client.post(
            "/chat/completions",
            json={
                "model": os.environ.get("CLANKER_MODEL", "deepseek-chat"),
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
        )
        print(f"Test connection: {response.status_code}", file=sys.stderr)
        # Drain response to avoid connection reuse issues
        _ = response.content
    except Exception as e:
        print(f"Test connection failed: {e}", file=sys.stderr)
        # Don't raise; the client will fail later if truly unreachable

    return client


# ─── System Prompt Construction ──────────────────────────────
def load_skill_file() -> str:
    skill_file = SKILLS_PATH / "SKILL.md"
    if skill_file.exists():
        return skill_file.read_text()
    return ""

def build_system_prompt() -> str:
    base = """You are an expert software engineer working in a containerized sandbox.
You have access to a workspace at /workspace containing a codebase.
Use the provided tools to read files, search code, run commands, and make changes.
Be concise and accurate. Always explain your reasoning before making changes.
When editing files, use precise diffs."""
    skills = load_skill_file()
    if skills:
        base += f"\n\n## Project Conventions (SKILL.md):\n{skills}"
    return base


# ─── Turn logging ──────────────────────────────
def _log_event(session_file: Path, event_type: str, **kwargs) -> None:
    with session_file.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            **kwargs
        }) + "\n")


# ─── The Agent Loop ──────────────────────────────────────────
# ─── Response draining helpers ──────────────────────────────
def _make_request_body(messages, use_stream: bool) -> dict:
    return {
        "model": os.environ.get("CLANKER_MODEL", "deepseek-chat"),
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
        "stream": use_stream,
    }


class TurnResult:
    """Summary of a single LLM call: text content, tool calls, and usage."""
    __slots__ = ("content", "tool_calls", "usage")

    def __init__(self, content="", tool_calls=None, usage=None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}

    @property
    def is_final(self) -> bool:
        """A pure text answer with no tool calls terminates the turn."""
        return bool(self.content) and not self.tool_calls


def _add_tool_call_chunk(tool_calls, tc_delta) -> None:
    """Accumulate a streaming tool-call delta into the (mutable) tool_calls list."""
    idx = tc_delta.get("index", 0)
    while len(tool_calls) <= idx:
        tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
    if "id" in tc_delta:
        tool_calls[idx]["id"] = tc_delta["id"]
    if "type" in tc_delta:
        tool_calls[idx]["type"] = tc_delta["type"]
    fn = tc_delta.get("function")
    if fn:
        if "name" in fn:
            tool_calls[idx]["function"]["name"] += fn["name"]
        if "arguments" in fn:
            tool_calls[idx]["function"]["arguments"] += fn["arguments"]


def _drain_stream(client, body, stream_to, verbose) -> TurnResult:
    """Read a streaming SSE response and assemble content / tool_calls / usage."""
    result = TurnResult()
    with client.stream("POST", "/chat/completions", json=body, timeout=120) as response:
        for n, line in enumerate(response.iter_lines()):
            if verbose:
                print(f"line {n+1}: {line}", file=sys.stderr)
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            # DeepSeek/OpenAI streaming APIs include cumulative usage on the
            # final data chunk of each response.
            if chunk.get("usage"):
                result.usage = dict(chunk["usage"])
            delta = chunk["choices"][0]["delta"]
            if delta.get("content"):
                result.content += delta["content"]
                stream_to(delta["content"], end="")
            if "tool_calls" in delta:
                for tc_delta in delta["tool_calls"]:
                    _add_tool_call_chunk(result.tool_calls, tc_delta)
    return result


def _drain_nonstream(client, body, stream_to, verbose) -> TurnResult:
    """Read a plain JSON response and assemble content / tool_calls / usage."""
    result = TurnResult()
    with client.stream("POST", "/chat/completions", json=body, timeout=120) as response:
        for n, line in enumerate(response.iter_lines()):
            if verbose:
                print(f"line {n+1}: {line}", file=sys.stderr)
            if not line:
                continue
            chunk = json.loads(line)
            if chunk.get("usage"):
                result.usage = dict(chunk["usage"])
            message = chunk["choices"][0]["message"]
            if message.get("content"):
                result.content += message["content"]
                stream_to(message["content"])
            # NB: tool_calls in non-streaming mode are not supported yet.
    return result


def _run_tool_calls(tool_calls, messages, stream_to, content=None) -> None:
    """Append the assistant msg + execute each tool, appending tool results."""
    messages.append({
        "role": "assistant",
        "type": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    })
    for tc in tool_calls:
        fn_name = tc["function"]["name"]
        fn_args = json.loads(tc["function"]["arguments"])
        stream_to(f"\n[Running {fn_name}...]")
        result = execute_tool(fn_name, fn_args)
        if len(result) > 8000:
            result = result[:8000] + "\n... [truncated]"
        messages.append({
            "role": "tool",
            "type": "tool",
            "tool_call_id": tc["id"],
            "content": result,
        })


# ─── The Agent Loop ──────────────────────────────────────────
def run_agent(
    prompt: str,
    stream_to: Callable = print,
    mode="interactive",
    use_stream: bool = True,
    verbose: bool = False,
    max_turns: int = 30,
    session_file: Path | None = None,
) -> str:

    started = time.perf_counter()

    if session_file:
        _log_event(
            session_file,
            "session_start",
            project=os.environ.get("CLANKER_PROJECT_NAME", "unknown"),
            session_id=os.environ.get("CLANKER_PROJECT_NAME", "unknown"),
        )

    client = get_client()
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    final = ""

    for turn in range(1, max_turns + 1):  # safety limit
        if session_file:
            _log_event(session_file, "user", content=prompt)

        body = _make_request_body(messages, use_stream)
        if verbose:
            print(f"Sending to proxy: {json.dumps(body)}", file=sys.stderr)

        # Exchange one round-trip with the model.
        drain = _drain_stream if use_stream else _drain_nonstream
        result = drain(client, body, stream_to, verbose)

        # Persist the assistant output for this turn.
        if session_file:
            _log_event(
                session_file,
                "assistant",
                content=result.content or None,
                tool_calls=result.tool_calls or None,
            )
            if result.usage:
                _log_event(session_file, "llm_call", turn=turn, usage=result.usage)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total_usage[k] = total_usage.get(k, 0) + result.usage.get(k, 0)

        if verbose:
            print(f"tool_calls: {result.tool_calls}", file=sys.stderr)

        # A pure text answer ends the turn; otherwise run any requested tools.
        if result.is_final:
            stream_to("")
            final = result.content
            break

        if result.tool_calls:
            _run_tool_calls(result.tool_calls, messages, stream_to, content=result.content)
        else:
            # No content and no tools: nothing useful arrived; bail out.
            final = result.content
            break

    if verbose:
        print(f"full_content: {final}", file=sys.stderr)

    if session_file:
        _log_event(
            session_file, "session_end",
            duration_seconds=time.perf_counter() - started,
            turns=turn,
            usage=total_usage,
        )

    return final



# ─── Session management ───────────────────────────────────────────────
def list_sessions(project_sessions_dir: Path):
    """Print available session files for the current project."""

    if not project_sessions_dir.exists():
        print(f"No sessions found in {project_sessions_dir}")
        return
    files = sorted(project_sessions_dir.glob("*.jsonl"))
    if not files:
        print(f"No session files in {project_sessions_dir}")
        return
    print(f"Sessions for {project_sessions_dir.parent.name}/{project_sessions_dir.name}:")
    for f in files:
        # Read the first and last event to show a summary
        try:
            with f.open("r") as fh:
                first_line = fh.readline()
                # Read last non-empty line
                last_line = ""
                for line in fh:
                    if line.strip():
                        last_line = line
            first_event = json.loads(first_line) if first_line else {}
            last_event = json.loads(last_line) if last_line else {}
            first_ts = first_event.get("ts", "?")
            last_ts = last_event.get("ts", "?")
            first_type = first_event.get("type", "?")
            last_type = last_event.get("type", "?")
            print(f"  {f.name}  [{first_ts} → {last_ts}]  ({first_type} → {last_type})")
        except Exception as e:
            print(f"  {f.name}  [unreadable: {e}]")



# ─── CLI Modes ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Clanker agent loop")
    parser.add_argument("prompt", nargs="*", help="User prompt")
    parser.add_argument("--pipe", action="store_true", help="Read context from stdin")
    parser.add_argument("--json", action="store_true", help="JSON protocol mode")
    parser.add_argument("--oneshot", action="store_true", help="Single question, no tools")
    parser.add_argument("--resume", metavar="SESSION_FILE", help="Resume from a session JSONL file")
    parser.add_argument("--list-sessions", action="store_true", help="List available session files")
    args = parser.parse_args()

    # ─── Session file handling ─────────────────────────────
    sessions_dir = Path(os.environ.get(
        "CLANKER_SESSIONS_DIR",
        str(Path.home() / ".clanker" / "sessions")
    ))
    project_name = os.environ.get("CLANKER_PROJECT_NAME", "unknown")
    project_sessions_dir = sessions_dir / project_name

    if args.list_sessions:
        list_sessions(project_sessions_dir)
        return

    session_file = None
    if args.resume:
        # Resume from a specific file
        resume_path = Path(args.resume)
        if not resume_path.is_absolute():
            resume_path = project_sessions_dir / resume_path
        if not resume_path.exists():
            print(f"Session file not found: {resume_path}", file=sys.stderr)
            sys.exit(1)
        session_file = resume_path
        print(f"Resuming session: {session_file}", file=sys.stderr)
    else:
        # Create a new session file
        project_sessions_dir.mkdir(parents=True, exist_ok=True)
        session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
        session_file = project_sessions_dir / f"{session_id}.jsonl"
        # Touch the file to ensure it exists
        session_file.touch()
        print(f"New session: {session_file}", file=sys.stderr)

    # ─── Dispatch based on mode ────────────────────────────
    if args.json:
        run_json_mode(session_file)
        return

    if args.pipe:
        context = sys.stdin.read()
        prompt = " ".join(args.prompt)
        full_prompt = f"Context:\n{context}\n\n---\n\nInstruction: {prompt}"
        run_agent(full_prompt, session_file=session_file)
        return

    if args.oneshot:
        prompt = " ".join(args.prompt)
        run_agent(prompt + " (Provide a concise answer without using tools.)",
                  session_file=session_file, mode="oneshot")
        return

    # Default interactive mode
    if args.prompt:
        prompt = " ".join(args.prompt)
    else:
        prompt = input("clanker> ")
    run_agent(prompt, session_file=session_file)

def run_json_mode():
    """Read JSON request from stdin, process, write JSON responses to stdout."""
    try:
        request = json.loads(sys.stdin.read())
    except Exception:
        return
    prompt = request.get("prompt", "")
    context = request.get("context", "")
    full_prompt = prompt
    if context:
        full_prompt = f"Context:\n{context}\n\n---\n\nInstruction: {prompt}"

    # Use a streaming callback that prints JSON events
    def stream_callback(text, end="\n"):
        sys.stdout.write(json.dumps({"type": "text", "content": text + end}) + "\n")
        sys.stdout.flush()

    run_agent(full_prompt, stream_to=stream_callback, verbose=args.verbose)
    sys.stdout.write(json.dumps({"type": "finished"}) + "\n")

if __name__ == "__main__":
    main()
