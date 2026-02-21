"""Custom fetcher example - GitHub API fetcher."""
import asyncio
from datetime import datetime
from typing import Optional

import httpx

from omni_fetcher import OmniFetcher, source, BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.structured import JSONData


@source(
    name="github",
    uri_patterns=["github.com", "api.github.com"],
    mime_types=["application/json"],
    priority=15,
    description="Fetch data from GitHub API"
)
class GitHubFetcher(BaseFetcher):
    """Fetcher for GitHub API endpoints."""
    
    name = "github"
    priority = 15
    
    def __init__(self, token: Optional[str] = None):
        super().__init__()
        self.token = token
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        return "github.com" in uri.lower()
    
    async def fetch(self, uri: str, **kwargs):
        api_url = self._convert_to_api_url(uri)
        
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "OmniFetcher-GitHub-Example"
        }
        
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        
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
        if "api.github.com" in uri:
            return uri
        
        uri = uri.replace("https://github.com/", "")
        uri = uri.replace("http://github.com/", "")
        
        if uri.startswith("orgs/"):
            return f"https://api.github.com/{uri}"
        elif uri.startswith("repos/"):
            return f"https://api.github.com/{uri}"
        else:
            return f"https://api.github.com/users/{uri.strip('/')}"


async def main():
    print("=" * 60)
    print("Custom GitHub Fetcher Example")
    print("=" * 60)
    
    fetcher = OmniFetcher()
    
    print("\nRegistered sources:")
    for s in fetcher.list_sources():
        print(f"  - {s}")
    
    print("\nFetching GitHub user 'octocat'...")
    try:
        result = await fetcher.fetch("https://api.github.com/users/octocat")
        print(f"\nLogin: {result.data.get('login')}")
        print(f"Name: {result.data.get('name')}")
        print(f"Followers: {result.data.get('followers')}")
    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
