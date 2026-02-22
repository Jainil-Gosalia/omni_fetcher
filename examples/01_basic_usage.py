"""Basic usage examples for OmniFetcher."""

import asyncio
import os

from omni_fetcher import OmniFetcher


async def main():
    fetcher = OmniFetcher()

    print("=" * 50)
    print("Example 1: Fetch JSON from API")
    print("=" * 50)

    try:
        result = await fetcher.fetch("https://jsonplaceholder.typicode.com/users/1")
        print(f"Type: {type(result).__name__}")
        print(f"Name: {result.data.get('name', 'N/A')}")
        print(f"Root keys: {result.root_keys}")
    except Exception as e:
        print(f"Error: {e}")

    print()
    print("=" * 50)
    print("Example 2: List all sources")
    print("=" * 50)

    sources = fetcher.list_sources()
    for source_name in sources:
        info = fetcher.get_source_info(source_name)
        print(f"  - {source_name} (priority: {info.priority})")


if __name__ == "__main__":
    asyncio.run(main())
