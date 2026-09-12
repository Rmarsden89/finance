from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from finance.broker.robinhood_mcp import RobinhoodMCPClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authenticate directly to Robinhood Trading MCP and export tool schemas."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/robinhood_mcp_tools.json"),
    )
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    client = RobinhoodMCPClient()

    print("ROBINHOOD TRADING MCP DISCOVERY", flush=True)
    print("Connecting directly to Robinhood; no LLM is involved.", flush=True)
    tools = await client.list_tools()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(tools, indent=2), encoding="utf-8")

    print(f"Tools discovered: {len(tools)}", flush=True)
    for tool in tools:
        print(f"  {tool.get('name')}", flush=True)
    print(f"Schema export:    {args.output}", flush=True)


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
