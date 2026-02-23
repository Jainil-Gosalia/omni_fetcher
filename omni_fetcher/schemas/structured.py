"""Structured data Pydantic models for OmniFetcher."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from omni_fetcher.schemas.base import BaseFetchedData, DataCategory, MediaType


class BaseStructuredData(BaseFetchedData):
    """Base model for structured data (JSON, YAML, XML, etc.)."""

    data: Any = Field(default=None, description="The structured data")

    category: DataCategory = DataCategory.STRUCTURED


class JSONData(BaseStructuredData):
    """Model for JSON data."""

    media_type: MediaType = MediaType.TEXT_JSON
    root_keys: Optional[list[str]] = Field(None, description="Top-level keys")
    json_schema: Optional[dict[str, Any]] = Field(None, description="JSON schema if validated")
    is_array: bool = Field(False, description="Whether root is an array")
    array_length: Optional[int] = Field(None, ge=0, description="Array length if root is array")
    item_type: Optional[str] = Field(None, description="Type of items in array")
    max_depth: Optional[int] = Field(None, ge=0, description="Maximum nesting depth")


class YAMLData(BaseStructuredData):
    """Model for YAML data."""

    media_type: MediaType = MediaType.TEXT_YAML
    root_keys: Optional[list[str]] = Field(None, description="Top-level keys")
    has_anchors: bool = Field(False, description="Whether YAML uses anchors/aliases")
    anchors_used: Optional[list[str]] = Field(None, description="List of anchor names used")


class XMLData(BaseStructuredData):
    """Model for XML data."""

    media_type: MediaType = MediaType.TEXT_XML
    content: str = Field(..., description="Raw XML content")
    root_element: str = Field(..., description="Root element name")
    elements: Optional[list[str]] = Field(None, description="All element names")
    attributes: Optional[dict[str, str]] = Field(None, description="Root element attributes")
    namespaces: Optional[dict[str, str]] = Field(None, description="XML namespaces")


class GraphQLResponse(BaseStructuredData):
    """Model for GraphQL response."""

    media_type: MediaType = MediaType.TEXT_JSON

    # Request fields (what was sent)
    query: str = Field(..., description="The GraphQL query string")
    operation_name: Optional[str] = Field(None, description="Operation name if specified")
    variables: Optional[dict[str, Any]] = Field(None, description="Query variables")

    # Response fields
    errors: Optional[list[dict[str, Any]]] = Field(None, description="GraphQL errors if any")
    extensions: Optional[dict[str, Any]] = Field(None, description="Response extensions")

    # Convenience properties
    has_errors: bool = Field(False, description="Whether response has errors")

    # Pydantic model validation
    def __init__(self, **data):
        super().__init__(**data)
        # Auto-set has_errors
        if self.errors:
            object.__setattr__(self, "has_errors", bool(self.errors))
