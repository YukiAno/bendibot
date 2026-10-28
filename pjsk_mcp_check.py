"""最终验证：从 .env 读取 MCP 配置 → 连上 → 调用工具。

这一步证明「用户重启机器人之后真的能用」，而不只是我硬编码的配置能用。
"""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("LOG_LEVEL", "ERROR")

from dotenv import dotenv_values  # noqa: E402

import nonebot  # noqa: E402

nonebot.init()
nonebot.load_plugin("nonebot_plugin_llmchat")

from nonebot_plugin_llmchat.config import MCPServerConfig  # noqa: E402
from nonebot_plugin_llmchat.mcpclient import MCPClient  # noqa: E402


async def main() -> None:
    print("=" * 68)
    print("1) 解析 .env 里的 LLMCHAT__MCP_SERVERS")
    print("=" * 68)
    values = dotenv_values(str(BASE / ".env"))
    raw = values.get("LLMCHAT__MCP_SERVERS")
    if not raw:
        print("  ❌ .env 里没有 LLMCHAT__MCP_SERVERS")
        return

    parsed = json.loads(raw)
    print("  JSON 合法 ✅  服务器：", list(parsed))

    servers = {}
    for name, item in parsed.items():
        servers[name] = MCPServerConfig(**item)
        print("  %s -> friendly_name=%s" % (name, servers[name].friendly_name))
        print("       command =", servers[name].command)
        print("       args    =", servers[name].args)

    # 校验文件真的存在
    for name, server in servers.items():
        if server.command and not Path(server.command).exists():
            print("  ❌ python 路径不存在:", server.command)
            return
        for arg in server.args or []:
            if arg.endswith(".py") and not Path(arg).exists():
                print("  ❌ 脚本不存在:", arg)
                return
    print("  路径检查 ✅")

    print()
    print("=" * 68)
    print("2) 连上并调用（完全走 .env 的配置）")
    print("=" * 68)
    client = MCPClient.get_instance(servers, str(BASE), 60)

    tools = await client.get_available_tools(is_group=True)
    mine = [t for t in tools if t["function"]["name"].startswith("mcp__")]
    print("  拉到工具 %d 个，其中 MCP 工具 %d 个" % (len(tools), len(mine)))
    for tool in mine:
        print("   •", tool["function"]["name"])

    print()
    checks = [
        ("mcp__pjsk__get_event", {"region": "cn"}),
        ("mcp__pjsk__calc_effect", {"skills": "200 100 100 100 100"}),
    ]
    for tool_name, arguments in checks:
        result = await client.call_tool(tool_name, arguments)
        text = str(result)
        # FastMCP 返回 [TextContent(...)]，抠出 text=
        if "text='" in text:
            start = text.index("text='") + 6
            end = text.rindex("', annotations=")
            text = text[start:end].replace("\\n", "\n")
        print("  ✅ %s" % tool_name.replace("mcp__pjsk__", ""))
        for line in text.splitlines()[:8]:
            print("       " + line)
        print()

    print("=" * 68)
    print("结论：重启机器人后 AI 就能调用这些工具了")
    print("=" * 68)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()