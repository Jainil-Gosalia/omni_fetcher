"""Tests for structured data Pydantic models."""

from datetime import datetime

from omni_fetcher.schemas.structured import (
    BaseStructuredData,
    JSONData,
    YAMLData,
    XMLData,
    GraphQLResponse,
)
from omni_fetcher.schemas.base import FetchMetadata, MediaType, DataCategory


class TestBaseStructuredData:
    def test_base_structured_data_creation(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/data",
            fetched_at=datetime.now(),
            mime_type="application/json",
        )
        data = BaseStructuredData(
            metadata=metadata,
            category=DataCategory.STRUCTURED,
            media_type=MediaType.TEXT_JSON,
            data={"key": "value"},
        )
        assert data.data == {"key": "value"}


class TestJSONData:
    def test_json_data_creation(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/users",
            fetched_at=datetime.now(),
            mime_type="application/json",
        )
        json_data = JSONData(
            metadata=metadata,
            data={"users": [{"id": 1, "name": "John"}]},
            root_keys=["users"],
            json_schema=None,
            is_array=False,
        )
        assert "users" in json_data.data
        assert json_data.root_keys == ["users"]
        assert json_data.is_array is False

    def test_json_data_with_json_schema(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/user",
            fetched_at=datetime.now(),
            mime_type="application/json",
        )
        json_data = JSONData(
            metadata=metadata,
            data={"id": 1, "name": "John"},
            json_schema={"type": "object", "properties": {"id": {"type": "integer"}}},
        )
        assert json_data.json_schema is not None

    def test_json_data_array(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/items",
            fetched_at=datetime.now(),
        )
        json_data = JSONData(
            metadata=metadata,
            data=[1, 2, 3, 4, 5],
            is_array=True,
            array_length=5,
            item_type="integer",
        )
        assert json_data.is_array is True
        assert json_data.array_length == 5

    def test_json_data_nested(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/nested",
            fetched_at=datetime.now(),
        )
        json_data = JSONData(
            metadata=metadata,
            data={"a": {"b": {"c": "value"}}},
            max_depth=3,
        )
        assert json_data.max_depth == 3


class TestYAMLData:
    def test_yaml_data_creation(self):
        metadata = FetchMetadata(
            source_uri="file:///config/app.yaml",
            fetched_at=datetime.now(),
            mime_type="application/x-yaml",
        )
        yaml_data = YAMLData(
            metadata=metadata,
            data={"database": {"host": "localhost", "port": 5432}},
            root_keys=["database"],
        )
        assert "database" in yaml_data.data
        assert yaml_data.data["database"]["port"] == 5432

    def test_yaml_data_with_anchors(self):
        metadata = FetchMetadata(
            source_uri="file:///config/deploy.yaml",
            fetched_at=datetime.now(),
        )
        yaml_data = YAMLData(
            metadata=metadata,
            data={"defaults": {"timeout": 30}, "service": {"timeout": "<ref:defaults.timeout>"}},
            has_anchors=True,
            anchors_used=["defaults.timeout"],
        )
        assert yaml_data.has_anchors is True


class TestXMLData:
    def test_xml_data_creation(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/data.xml",
            fetched_at=datetime.now(),
            mime_type="application/xml",
        )
        xml_data = XMLData(
            metadata=metadata,
            content="<root><item>value</item></root>",
            root_element="root",
            elements=["root", "item"],
            attributes={},
            namespaces=None,
        )
        assert xml_data.root_element == "root"
        assert "item" in xml_data.elements

    def test_xml_data_with_namespaces(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/soap.xml",
            fetched_at=datetime.now(),
        )
        xml_content = '<root xmlns:ns="http://example.com/ns"></root>'
        xml_data = XMLData(
            metadata=metadata,
            content=xml_content,
            root_element="root",
            elements=["root"],
            namespaces={"ns": "http://example.com/ns"},
        )
        assert "ns" in xml_data.namespaces

    def test_xml_data_with_attributes(self):
        metadata = FetchMetadata(
            source_uri="https://example.com/attr.xml",
            fetched_at=datetime.now(),
        )
        xml_content = '<item id="1" type="test">Value</item>'
        xml_data = XMLData(
            metadata=metadata,
            content=xml_content,
            root_element="item",
            elements=["item"],
            attributes={"id": "1", "type": "test"},
        )
        assert xml_data.attributes["id"] == "1"


class TestGraphQLResponse:
    def test_graphql_response_creation(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/graphql",
            fetched_at=datetime.now(),
            mime_type="application/json",
        )
        response = GraphQLResponse(
            metadata=metadata,
            data={"user": {"id": 1, "name": "John"}},
            query="query { user { id name } }",
            variables=None,
            errors=None,
        )
        assert "user" in response.data
        assert response.errors is None

    def test_graphql_response_with_errors(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/graphql",
            fetched_at=datetime.now(),
        )
        response = GraphQLResponse(
            metadata=metadata,
            data=None,
            query="query { user { id name } }",
            errors=[{"message": "User not found", "locations": [{"line": 1, "column": 1}]}],
        )
        assert response.errors is not None
        assert len(response.errors) == 1

    def test_graphql_response_with_variables(self):
        metadata = FetchMetadata(
            source_uri="https://api.example.com/graphql",
            fetched_at=datetime.now(),
        )
        response = GraphQLResponse(
            metadata=metadata,
            data={"user": {"id": 1}},
            query="query GetUser($id: ID!) { user(id: $id) { id } }",
            variables={"id": 1},
        )
        assert response.variables == {"id": 1}


class TestStructuredInheritance:
    def test_json_data_is_structured(self):
        assert issubclass(JSONData, BaseStructuredData)

    def test_yaml_data_is_structured(self):
        assert issubclass(YAMLData, BaseStructuredData)

    def test_xml_data_is_structured(self):
        assert issubclass(XMLData, BaseStructuredData)

    def test_graphql_response_is_structured(self):
        assert issubclass(GraphQLResponse, BaseStructuredData)
