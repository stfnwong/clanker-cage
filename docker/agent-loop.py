#!/usr/bin/env python3
"""agent-loop: The beating heart of clanker."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Iterable, Literal

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


# ─── The Agent Loop ──────────────────────────────────────────
def run_agent(
    prompt: str, 
    stream_to: Callable = print, 
    mode="interactive", 
    use_stream: bool=True, 
    verbose:bool=False,
    max_turns: int=30,
) -> str:
    client = get_client()
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    turn = 0
    while turn < max_turns:  # safety limit
        turn += 1
        # Prepare request
        body = {
            "model": os.environ.get("CLANKER_MODEL", "deepseek-chat"),
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "stream": use_stream,
        }

        if verbose:
            print(f"Sending to proxy: {json.dumps(body)}", file=sys.stderr)

        # Stream response
        full_content = ""
        tool_calls = []

        # Need full URL here
        with client.stream("POST", "/chat/completions", json=body, timeout=120) as response:
            for n, line in enumerate(response.iter_lines()):
                if verbose:
                    print(f"line {n+1}: {line}")

                # ==== Streaming mode
                # Text content
                if body["stream"] is True:
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk["choices"][0]["delta"]
                    if "content" in delta and delta["content"]:
                        full_content += delta["content"]
                        stream_to(delta["content"], end="")

                    # Tool calls
                    if "tool_calls" in delta:
                        for tc_delta in delta["tool_calls"]:
                            idx = tc_delta.get("index", 0)
                            # Ensure list is long enough
                            while len(tool_calls) <= idx:
                                tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                            if "id" in tc_delta:
                                tool_calls[idx]["id"] = tc_delta["id"]
                            if "type" in tc_delta:
                                tool_calls[idx]["type"] = tc_delta["type"]
                            if "function" in tc_delta:
                                if "name" in tc_delta["function"]:
                                    tool_calls[idx]["function"]["name"] += tc_delta["function"]["name"]
                                if "arguments" in tc_delta["function"]:
                                    tool_calls[idx]["function"]["arguments"] += tc_delta["function"]["arguments"]
                # ==== Non-streaming mode
                else:
                    chunk = json.loads(line)
                    message = chunk["choices"][0]["message"]
                    if "content" in message and message["content"]:
                        full_content += message["content"]
                        stream_to(message["content"])
                    # TODO: Add support for tool calls in non-streaming mode

        # After stream, decide next step
        if full_content and not tool_calls:
            # Pure text response, no tool calls -> final answer
            stream_to("")
            break

        if verbose:
            print(f"tool_calls: {tool_calls}")

        if tool_calls:
            assistant_msg = {
                "role": "assistant",
                "type": "assistant",
                "content": full_content or None,
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)
            
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

    if verbose:
        print(f"full_content: {full_content}")

    return full_content

# ─── CLI Modes ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Clanker agent loop")
    parser.add_argument("prompt", nargs="*", help="User prompt (for oneshot or interactive)")
    parser.add_argument("--pipe", action="store_true", help="Read context from stdin, prompt as argument")
    parser.add_argument("--json", action="store_true", help="JSON protocol for Neovim integration")
    parser.add_argument("--oneshot", action="store_true", help="Single question, no tool calls (just append 'Answer concisely')")
    parser.add_argument("--verbose", action="store_true", help="Set verbose mode", default=False)
    args = parser.parse_args()

    if args.json:
        # JSON mode for neovim: read JSON request, stream JSON events back
        run_json_mode()
        return

    if args.pipe:
        context = sys.stdin.read()
        prompt = " ".join(args.prompt)
        full_prompt = f"Context:\n{context}\n\n---\n\nInstruction: {prompt}"
        run_agent(full_prompt, stream_to=lambda s, end="": print(s, end=end, flush=True), verbose=args.verbose)
        return

    if args.oneshot:
        prompt = " ".join(args.prompt)
        run_agent(prompt + " (Provide a concise answer without using tools.)", mode="oneshot", verbose=args.verbose)
        return

    # Default interactive mode
    if args.prompt:
        prompt = " ".join(args.prompt)
    else:
        prompt = input("clanker> ")
    run_agent(prompt, verbose=args.verbose)


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
