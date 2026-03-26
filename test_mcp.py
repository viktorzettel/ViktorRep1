import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    server_params = StdioServerParameters(
        command="/Library/Frameworks/Python.framework/Versions/3.13/bin/notebooklm-mcp",
        args=[],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            print("\nListing NotebookLM Notebooks:")
            try:
                result = await session.call_tool("notebook_list", {})
                for content in result.content:
                    print(content.text)
            except Exception as e:
                print("Error calling tool:", e)

if __name__ == "__main__":
    asyncio.run(main())
