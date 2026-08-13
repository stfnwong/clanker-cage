#!/usr/bin/env python3
"""agent-loop: The beating heart of clanker."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Literal

import httpx  # pip install httpx

# ─── Configuration ──────────────────────────────────────────
SKILLS_PATH = Path(os.environ.get("CLANKER_SKILLS_PATH", "/etc/clanker/skills"))
WORKSPACE = Path("/workspace")
PROVIDER_SOCKET = Path("/var/run/provider.sock")
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
    if PROVIDER_MODE == "socket" and PROVIDER_SOCKET.exists():
        # Unix socket transport via httpx
        transport = httpx.HTTPTransport(uds=str(PROVIDER_SOCKET))
        return httpx.Client(transport=transport, base_url="http://localhost/")
    elif PROVIDER_MODE == "direct":
        api_key = os.environ.get("CLANKER_PROVIDER_KEY_FILE")
        if api_key and Path(api_key).exists():
            api_key = Path(api_key).read_text().strip()
        else:
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("CLANKER_PROVIDER_ENDPOINT", "https://api.deepseek.com/v1")
        return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"})
    else:
        raise RuntimeError("No provider configured.")

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
def run_agent(prompt: str, stream_to: callable = print, mode="interactive"):
    client = get_client()
    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": prompt},
    ]
    turn = 0
    while turn < 30:  # safety limit
        turn += 1
        # Prepare request
        body = {
            "model": os.environ.get("CLANKER_MODEL", "deepseek-chat"),
            "messages": messages,
            "tools": TOOLS,
            "tool_choice": "auto",
            "stream": True,
        }
        # Stream response
        full_content = ""
        tool_calls = []
        with client.stream("POST", "/chat/completions", json=body, timeout=120) as response:
            for line in response.iter_lines():
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
                # Text content
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
                        if "function" in tc_delta:
                            if "name" in tc_delta["function"]:
                                tool_calls[idx]["function"]["name"] += tc_delta["function"]["name"]
                            if "arguments" in tc_delta["function"]:
                                tool_calls[idx]["function"]["arguments"] += tc_delta["function"]["arguments"]
        # After stream, decide next step
        if full_content and not tool_calls:
            # Pure text response, no tool calls -> final answer
            stream_to("")
            break
        if tool_calls:
            # Add assistant message with tool calls to history
            assistant_msg = {
                "role": "assistant",
                "content": full_content or None,
                "tool_calls": tool_calls,
            }
            messages.append(assistant_msg)
            # Execute each tool and add results
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                fn_args = json.loads(tc["function"]["arguments"])
                stream_to(f"\n[Running {fn_name}...]")
                result = execute_tool(fn_name, fn_args)
                # Truncate huge results
                if len(result) > 8000:
                    result = result[:8000] + "\n... [truncated]"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": result,
                })
            # Continue loop to get next assistant response
        else:
            # No content and no tool calls? Unexpected, break
            break
    return full_content

# ─── CLI Modes ───────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Clanker agent loop")
    parser.add_argument("prompt", nargs="*", help="User prompt (for oneshot or interactive)")
    parser.add_argument("--pipe", action="store_true", help="Read context from stdin, prompt as argument")
    parser.add_argument("--json", action="store_true", help="JSON protocol for Neovim integration")
    parser.add_argument("--oneshot", action="store_true", help="Single question, no tool calls (just append 'Answer concisely')")
    args = parser.parse_args()

    if args.json:
        # JSON mode for neovim: read JSON request, stream JSON events back
        run_json_mode()
        return

    if args.pipe:
        context = sys.stdin.read()
        prompt = " ".join(args.prompt)
        full_prompt = f"Context:\n{context}\n\n---\n\nInstruction: {prompt}"
        run_agent(full_prompt, stream_to=lambda s, end="": print(s, end=end, flush=True))
        return

    if args.oneshot:
        prompt = " ".join(args.prompt)
        run_agent(prompt + " (Provide a concise answer without using tools.)", mode="oneshot")
        return

    # Default interactive mode
    if args.prompt:
        prompt = " ".join(args.prompt)
    else:
        prompt = input("clanker> ")
    run_agent(prompt)

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

    run_agent(full_prompt, stream_to=stream_callback)
    sys.stdout.write(json.dumps({"type": "finished"}) + "\n")

if __name__ == "__main__":
    main()
