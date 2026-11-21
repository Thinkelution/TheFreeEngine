"""Talk to /mcp with the official MCP Python SDK client. Usage: mcp_client_test.py <url> <api key>"""
import asyncio
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main(url, key):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=120) as hc, \
            streamable_http_client(url, http_client=hc) as streams:
        r, w = streams[0], streams[1]
        async with ClientSession(r, w) as s:
            info = await s.initialize()
            si = getattr(info, "server_info", None) or getattr(info, "serverInfo")
            pv = getattr(info, "protocol_version", None) or getattr(info, "protocolVersion")
            print("server:", si.name, "protocol", pv)
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            res = await s.call_tool("text_to_speech", {"text": "Hello from the MCP client.", "voice": "aria", "mood": "happy"})
            err = getattr(res, "is_error", None) if hasattr(res, "is_error") else res.isError
            print("tts:", [c.type for c in res.content], "error" if err else "ok")
            res = await s.call_tool("get_usage", {})
            err = getattr(res, "is_error", None) if hasattr(res, "is_error") else res.isError
            print("usage ok:", not err)


asyncio.run(main(sys.argv[1], sys.argv[2]))
