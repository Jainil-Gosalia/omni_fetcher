"""Custom fetcher example - demonstrating the @source decorator."""

import asyncio
from datetime import datetime
from typing import Optional

import httpx

from omni_fetcher import OmniFetcher, source, BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.structured import JSONData


@source(
    name="bitbucket",
    uri_patterns=["bitbucket.org"],
    mime_types=["application/json"],
    priority=15,
    description="Fetch data from Bitbucket API",
)
class BitbucketFetcher(BaseFetcher):
    """Fetcher for Bitbucket API endpoints."""

    name = "bitbucket"
    priority = 15

    def __init__(self, token: Optional[str] = None):
        super().__init__()
        self.token = token

    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return "bitbucket.org" in uri.lower()

    async def fetch(self, uri: str, **kwargs):
        api_url = self._convert_to_api_url(uri)

        headers = {
            "Accept": "application/json",
            "User-Agent": "OmniFetcher-Bitbucket-Example",
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(api_url, headers=headers)
            response.raise_for_status()
            data = response.json()

        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type="application/json",
            status_code=response.status_code,
        )

        return JSONData(
            metadata=metadata,
            data=data,
            root_keys=list(data.keys()) if isinstance(data, dict) else None,
        )

    def _convert_to_api_url(self, uri: str) -> str:
        if "api.bitbucket.org" in uri:
            return uri

        uri = uri.replace("https://bitbucket.org/", "")
        uri = uri.replace("http://bitbucket.org/", "")

        return f"https://api.bitbucket.org/2.0/{uri.strip('/')}"


async def main():
    print("=" * 60)
    print("Custom Fetcher Example - Bitbucket API")
    print("=" * 60)

    fetcher = OmniFetcher()

    print("\nRegistered sources:")
    for s in fetcher.list_sources():
        print(f"  - {s}")

    print("\nNote: Bitbucket API requires authentication.")
    print("This example demonstrates how to create a custom fetcher.")
    print("The actual fetch would require a valid Bitbucket token.")


if __name__ == "__main__":
    asyncio.run(main())
