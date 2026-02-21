"""Authentication configuration for OmniFetcher."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    """Authentication configuration for a source.
    
    Values can be provided directly or loaded from environment variables.
    Environment variables take precedence over direct values.
    """
    
    # Auth type: bearer, api_key, basic, aws
    type: str = Field(default="bearer", description="Authentication type")
    
    # Bearer token
    token: Optional[str] = Field(None, description="Bearer token")
    token_env: Optional[str] = Field(None, description="Env var name for bearer token")
    
    # API Key
    api_key: Optional[str] = Field(None, description="API key value")
    api_key_header: str = Field(default="X-API-Key", description="API key header name")
    api_key_env: Optional[str] = Field(None, description="Env var name for API key")
    
    # Basic Auth
    username: Optional[str] = Field(None, description="Basic auth username")
    username_env: Optional[str] = Field(None, description="Env var name for username")
    password: Optional[str] = Field(None, description="Basic auth password")
    password_env: Optional[str] = Field(None, description="Env var name for password")
    
    # AWS
    aws_access_key_id: Optional[str] = Field(None, description="AWS access key ID")
    aws_access_key_id_env: str = Field(default="AWS_ACCESS_KEY_ID", description="AWS access key ID env var")
    aws_secret_access_key: Optional[str] = Field(None, description="AWS secret access key")
    aws_secret_access_key_env: str = Field(default="AWS_SECRET_ACCESS_KEY", description="AWS secret access key env var")
    aws_region: str = Field(default="us-east-1", description="AWS region")
    aws_region_env: Optional[str] = Field(None, description="Env var name for AWS region")
    
    # OAuth2
    oauth2_client_id: Optional[str] = Field(None, description="OAuth2 client ID")
    oauth2_client_id_env: Optional[str] = Field(None, description="Env var name for OAuth2 client ID")
    oauth2_client_secret: Optional[str] = Field(None, description="OAuth2 client secret")
    oauth2_client_secret_env: Optional[str] = Field(None, description="Env var name for OAuth2 client secret")
    oauth2_token_url: Optional[str] = Field(None, description="OAuth2 token URL")
    oauth2_scope: Optional[str] = Field(None, description="OAuth2 scope")
    oauth2_grant_type: str = Field(default="client_credentials", description="OAuth2 grant type: client_credentials, refresh_token")
    oauth2_refresh_token: Optional[str] = Field(None, description="OAuth2 refresh token")
    oauth2_refresh_token_env: Optional[str] = Field(None, description="Env var name for OAuth2 refresh token")
    oauth2_access_token: Optional[str] = Field(None, description="OAuth2 access token (cached)")
    oauth2_token_expiry: Optional[float] = Field(None, description="OAuth2 token expiry timestamp")
    
    def get_token(self) -> Optional[str]:
        """Get bearer token, checking env var first."""
        if self.token_env:
            return os.environ.get(self.token_env, self.token)
        return self.token
    
    def get_api_key(self) -> Optional[str]:
        """Get API key, checking env var first."""
        if self.api_key_env:
            return os.environ.get(self.api_key_env, self.api_key)
        return self.api_key
    
    def get_username(self) -> Optional[str]:
        """Get username, checking env var first."""
        if self.username_env:
            return os.environ.get(self.username_env, self.username)
        return self.username
    
    def get_password(self) -> Optional[str]:
        """Get password, checking env var first."""
        if self.password_env:
            return os.environ.get(self.password_env, self.password)
        return self.password
    
    def get_aws_credentials(self) -> Dict[str, Optional[str]]:
        """Get AWS credentials as dict."""
        return {
            "aws_access_key_id": os.environ.get(self.aws_access_key_id_env, self.aws_access_key_id),
            "aws_secret_access_key": os.environ.get(self.aws_secret_access_key_env, self.aws_secret_access_key),
            "region_name": os.environ.get(self.aws_region_env or "", self.aws_region),
        }
    
    def get_oauth2_credentials(self) -> Dict[str, Optional[str]]:
        """Get OAuth2 credentials as dict."""
        return {
            "client_id": os.environ.get(self.oauth2_client_id_env, self.oauth2_client_id) if self.oauth2_client_id_env else self.oauth2_client_id,
            "client_secret": os.environ.get(self.oauth2_client_secret_env, self.oauth2_client_secret) if self.oauth2_client_secret_env else self.oauth2_client_secret,
        }
    
    def get_oauth2_token_url(self) -> Optional[str]:
        """Get OAuth2 token URL."""
        return self.oauth2_token_url
    
    def get_oauth2_refresh_token(self) -> Optional[str]:
        """Get OAuth2 refresh token, checking env var first."""
        if self.oauth2_refresh_token_env:
            return os.environ.get(self.oauth2_refresh_token_env, self.oauth2_refresh_token)
        return self.oauth2_refresh_token
    
    def get_oauth2_access_token(self) -> Optional[str]:
        """Get current OAuth2 access token."""
        return self.oauth2_access_token
    
    def set_oauth2_token(self, access_token: str, expires_in: Optional[float] = None) -> None:
        """Set OAuth2 access token and expiry."""
        self.oauth2_access_token = access_token
        if expires_in:
            self.oauth2_token_expiry = time.time() + expires_in
    
    def is_oauth2_token_valid(self) -> bool:
        """Check if OAuth2 token is still valid."""
        if not self.oauth2_access_token:
            return False
        if self.oauth2_token_expiry:
            return time.time() < self.oauth2_token_expiry - 30
        return True
    
    async def refresh_oauth2_token(self) -> Optional[str]:
        """Refresh OAuth2 access token.
        
        Supports client_credentials and refresh_token grant types.
        Returns the new access token or None if refresh failed.
        """
        token_url = self.get_oauth2_token_url()
        if not token_url:
            return None
        
        creds = self.get_oauth2_credentials()
        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")
        
        if not client_id or not client_secret:
            return None
        
        data: Dict[str, str] = {
            "client_id": client_id,
            "client_secret": client_secret,
        }
        
        if self.oauth2_grant_type == "refresh_token":
            refresh_token = self.get_oauth2_refresh_token()
            if not refresh_token:
                return None
            data["grant_type"] = "refresh_token"
            data["refresh_token"] = refresh_token
        else:
            data["grant_type"] = "client_credentials"
            if self.oauth2_scope:
                data["scope"] = self.oauth2_scope
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(token_url, data=data)
                response.raise_for_status()
                token_data = response.json()
                
                access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in")
                
                if access_token:
                    self.set_oauth2_token(access_token, expires_in)
                    return access_token
        except Exception:
            pass
        
        return None
    
    def get_headers(self) -> Dict[str, str]:
        """Get HTTP headers for this auth config."""
        headers = {}
        
        if self.type == "bearer":
            token = self.get_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        
        elif self.type == "api_key":
            key = self.get_api_key()
            if key:
                headers[self.api_key_header] = key
        
        elif self.type == "basic":
            import base64
            username = self.get_username()
            password = self.get_password()
            if username and password:
                creds = base64.b64encode(f"{username}:{password}".encode()).decode()
                headers["Authorization"] = f"Basic {creds}"
        
        elif self.type == "oauth2":
            access_token = self.get_oauth2_access_token()
            if access_token:
                headers["Authorization"] = f"Bearer {access_token}"
        
        return headers
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict (for storage), masking sensitive values."""
        d = self.model_dump()
        
        # Mask sensitive values
        if d.get("token") and len(d["token"]) > 8:
            d["token"] = d["token"][:4] + "****" + d["token"][-4:]
        if d.get("api_key") and len(d.get("api_key", "")) > 8:
            d["api_key"] = d["api_key"][:4] + "****" + d["api_key"][-4:]
        if d.get("oauth2_client_secret") and len(d.get("oauth2_client_secret", "")) > 8:
            d["oauth2_client_secret"] = d["oauth2_client_secret"][:4] + "****" + d["oauth2_client_secret"][-4:]
        
        return d


def load_auth_from_env(prefix: str = "OMNI_") -> Dict[str, AuthConfig]:
    """Load auth configs from environment variables.
    
    Example env vars:
        OMNI_GITHUB_TYPE=bearer
        OMNI_GITHUB_TOKEN_ENV=GITHUB_TOKEN
        OMNI_S3_AWS_ACCESS_KEY_ID_ENV=AWS_ACCESS_KEY_ID
    """
    auth_configs = {}
    
    # Group env vars by source
    sources: Dict[str, Dict[str, str]] = {}
    
    for key, value in os.environ.items():
        if key.startswith(prefix):
            # Parse OMNI_SOURCE_PROPERTY or OMNI_SOURCE_OAUTH2_PROPERTY
            rest = key[len(prefix):]
            # Find first underscore to get source
            first_underscore = rest.find("_")
            if first_underscore > 0:
                source = rest[:first_underscore].lower()
                prop = rest[first_underscore + 1:]
                
                if source not in sources:
                    sources[source] = {}
                sources[source][prop] = value
    
    # Build AuthConfig for each source
    for source, props in sources.items():
        auth_type = props.get("TYPE", "bearer").lower()
        
        # Map properties to AuthConfig (convert props keys to lowercase)
        config = {"type": auth_type}
        props_lower = {k.lower(): v for k, v in props.items()}
        
        if auth_type == "bearer":
            if "token" in props_lower:
                config["token_env"] = props_lower["token"]
            elif "token_env" in props_lower:
                config["token_env"] = props_lower["token_env"]
        
        elif auth_type == "api_key":
            if "api_key" in props_lower:
                config["api_key"] = props_lower["api_key"]
            elif "api_key_env" in props_lower:
                config["api_key_env"] = props_lower["api_key_env"]
            if "api_key_header" in props_lower:
                config["api_key_header"] = props_lower["api_key_header"]
        
        elif auth_type == "aws":
            if "aws_access_key_id" in props_lower:
                config["aws_access_key_id"] = props_lower["aws_access_key_id"]
            elif "aws_access_key_id_env" in props_lower:
                config["aws_access_key_id_env"] = props_lower["aws_access_key_id_env"]
            if "aws_region" in props_lower:
                config["aws_region"] = props_lower["aws_region"]
        
        elif auth_type == "oauth2":
            if "oauth2_client_id" in props_lower:
                config["oauth2_client_id"] = props_lower["oauth2_client_id"]
            elif "oauth2_client_id_env" in props_lower:
                config["oauth2_client_id_env"] = props_lower["oauth2_client_id_env"]
            if "oauth2_client_secret" in props_lower:
                config["oauth2_client_secret"] = props_lower["oauth2_client_secret"]
            elif "oauth2_client_secret_env" in props_lower:
                config["oauth2_client_secret_env"] = props_lower["oauth2_client_secret_env"]
            if "oauth2_token_url" in props_lower:
                config["oauth2_token_url"] = props_lower["oauth2_token_url"]
            if "oauth2_scope" in props_lower:
                config["oauth2_scope"] = props_lower["oauth2_scope"]
            if "oauth2_grant_type" in props_lower:
                config["oauth2_grant_type"] = props_lower["oauth2_grant_type"]
            if "oauth2_refresh_token" in props_lower:
                config["oauth2_refresh_token"] = props_lower["oauth2_refresh_token"]
            elif "oauth2_refresh_token_env" in props_lower:
                config["oauth2_refresh_token_env"] = props_lower["oauth2_refresh_token_env"]
        
        auth_configs[source] = AuthConfig(**config)
    
    return auth_configs
