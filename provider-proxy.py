#!/usr/bin/env python3
"""
provider-proxy.py — Host-side Unix socket proxy for LLM API calls.
Holds API keys. Forwards requests to DeepSeek. Keeps keys out of containers.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

import httpx

# Configuration
SOCKET_PATH = Path(os.path.expanduser("~/.cache/clanker/provider.sock"))
API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com/v1"
MODEL = os.environ.get("CLANKER_MODEL", "deepseek-chat")

if not API_KEY:
    print("ERROR: DEEPSEEK_API_KEY not set. Export it and retry.", file=sys.stderr)
    sys.exit(1)


async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Handle a single connection from the container."""
    try:
        # Read the full request
        data = await reader.read()
        if not data:
            return

        request = json.loads(data)

        # The container sends the request body; we inject the API key
        body = request.get("body", {})
        body["model"] = body.get("model", MODEL)
        body["stream"] = body.get("stream", False)

        # Forward to DeepSeek
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST",
                f"{BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
            ) as response:
                # Stream response back to the container
                writer.write(f"HTTP/1.1 {response.status_code}\r\n".encode())
                writer.write(b"\r\n")

                async for chunk in response.aiter_bytes():
                    writer.write(chunk)
                    await writer.drain()

    except Exception as e:
        error_response = json.dumps({"error": str(e)}).encode()
        writer.write(error_response)
        await writer.drain()

    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    # Clean up stale socket
    if SOCKET_PATH.exists():
        SOCKET_PATH.unlink()

    # Ensure parent directory exists
    SOCKET_PATH.parent.mkdir(parents=True, exist_ok=True)

    server = await asyncio.start_unix_server(handle_client, path=SOCKET_PATH)
    print(f"Provider proxy listening on {SOCKET_PATH}", file=sys.stderr)
    print(f"Model: {MODEL}", file=sys.stderr)
    print(f"Mode: streaming proxy to {BASE_URL}", file=sys.stderr)

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
