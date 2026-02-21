"""S3 fetcher for OmniFetcher."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any, Optional

import boto3
from botocore.exceptions import ClientError

from omni_fetcher.core.registry import source
from omni_fetcher.fetchers.base import BaseFetcher
from omni_fetcher.schemas.base import FetchMetadata
from omni_fetcher.schemas.documents import TextDocument
from omni_fetcher.auth import AuthConfig


@source(
    name="s3",
    uri_patterns=["s3://", "s3.amazonaws.com"],
    priority=10,
    description="Fetch files from AWS S3",
)
class S3Fetcher(BaseFetcher):
    """Fetcher for AWS S3 objects.
    
    Supports auth via:
    - Constructor: S3Fetcher(aws_access_key_id='xxx', aws_secret_access_key='yyy')
    - set_auth: fetcher.set_auth(AuthConfig(type='aws', ...))
    - OmniFetcher auth registry
    """
    
    name = "s3"
    priority = 10
    
    def __init__(
        self,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: str = "us-east-1",
    ):
        super().__init__()
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self.region_name = region_name
    
    def set_auth(self, auth_config: AuthConfig) -> None:
        """Set auth from AuthConfig."""
        self._auth_config = auth_config
        
        if auth_config.type == "aws":
            creds = auth_config.get_aws_credentials()
            self.aws_access_key_id = creds.get("aws_access_key_id")
            self.aws_secret_access_key = creds.get("aws_secret_access_key")
            self.region_name = creds.get("region_name", "us-east-1")
    
    @classmethod
    def can_handle(cls, uri: str) -> bool:
        """Check if this is an S3 URI."""
        return uri.startswith("s3://") or ".s3.amazonaws.com" in uri
    
    async def fetch(self, uri: str, **kwargs: Any) -> Any:
        """Fetch an object from S3."""
        bucket, key = self._parse_s3_uri(uri)
        
        # Get object
        obj_data = await self._get_object(bucket, key)
        
        metadata = FetchMetadata(
            source_uri=uri,
            fetched_at=datetime.now(),
            source_name=self.name,
            mime_type=obj_data.get('content_type', 'application/octet-stream'),
            file_size=obj_data.get('content_length'),
        )
        
        content = obj_data['content']
        
        return TextDocument(
            metadata=metadata,
            content=content,
        )
    
    def _parse_s3_uri(self, uri: str) -> tuple[str, str]:
        """Parse S3 URI into bucket and key."""
        if uri.startswith("s3://"):
            uri = uri[5:]
            parts = uri.split("/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        elif ".s3.amazonaws.com" in uri:
            uri = uri.replace("https://", "").replace("http://", "")
            parts = uri.split(".s3.amazonaws.com/", 1)
            bucket = parts[0]
            key = parts[1] if len(parts) > 1 else ""
        else:
            raise ValueError(f"Invalid S3 URI: {uri}")
        
        return bucket, key
    
    async def _get_object(self, bucket: str, key: str) -> dict[str, Any]:
        """Get object from S3."""
        def _fetch():
            # Use credentials from auth config if set
            client = boto3.client(
                's3',
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.region_name,
            )
            
            try:
                response = client.get_object(Bucket=bucket, Key=key)
                content = response['Body'].read().decode('utf-8')
                
                return {
                    'content': content,
                    'content_type': response.get('ContentType', 'application/octet-stream'),
                    'content_length': response.get('ContentLength'),
                }
            except ClientError as e:
                raise ValueError(f"Failed to get S3 object: {e}")
        
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _fetch)
