"""Normalized auth types and the auth-resolver interface for v1.

Auth is normalized to a small canonical set -- ``bearer``, ``api_key``,
``basic``, ``oauth2``, ``aws`` -- that every source maps onto. Credentials
are injected per call and used transiently; OmniFetcher never stores them,
never auto-loads from the ambient environment by default, and never caches
or mutates tokens onto shared objects (see ``PHILOSOPHY.md`` section 7).

The concrete resolution behaviour belongs to a later issue; this file fixes
the credential value types and the ``AuthResolver`` interface.
"""

from __future__ import annotations

import base64
from enum import Enum
from typing import Literal, Optional, Protocol, Union, runtime_checkable

from pydantic import BaseModel, ConfigDict


class AuthType(str, Enum):
    """
    The canonical normalized auth types
    ===============================================
    The small, stable set every source maps its authentication onto.
    ===============================================
    NOTE:
        1. ``google_service_account`` is reconciled into this set (mapped
           under a canonical type) or documented as the lone exception in a
           later issue; it is intentionally absent here.

    Attributes
    ----------
        BEARER:
            Bearer token in an ``Authorization`` header.
        API_KEY:
            API key in a (source-defined) header.
        BASIC:
            HTTP basic auth (username + password).
        OAUTH2:
            OAuth2 access token (acquisition/refresh is the host's job).
        AWS:
            AWS access-key credentials (SigV4-style).
    """

    BEARER = "bearer"
    API_KEY = "api_key"
    BASIC = "basic"
    OAUTH2 = "oauth2"
    AWS = "aws"


class BearerAuth(BaseModel):
    """
    Bearer-token credential
    ===============================================
    A bearer token injected per call.
    ===============================================

    Attributes
    ----------
        type:
            Always ``AuthType.BEARER``.
        token:
            The bearer token value.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[AuthType.BEARER] = AuthType.BEARER
    token: str


class ApiKeyAuth(BaseModel):
    """
    API-key credential
    ===============================================
    An API key injected per call, with the header it is carried in.
    ===============================================

    Attributes
    ----------
        type:
            Always ``AuthType.API_KEY``.
        api_key:
            The API key value.
        header:
            The header name the key is sent in.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[AuthType.API_KEY] = AuthType.API_KEY
    api_key: str
    header: str = "X-API-Key"


class BasicAuth(BaseModel):
    """
    HTTP basic-auth credential
    ===============================================
    A username/password pair injected per call.
    ===============================================

    Attributes
    ----------
        type:
            Always ``AuthType.BASIC``.
        username:
            The basic-auth username.
        password:
            The basic-auth password.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[AuthType.BASIC] = AuthType.BASIC
    username: str
    password: str


class OAuth2Auth(BaseModel):
    """
    OAuth2 credential
    ===============================================
    A short-lived OAuth2 access token injected per call. Token acquisition
    and refresh are the host's responsibility; OmniFetcher never caches or
    refreshes tokens onto shared state.
    ===============================================

    Attributes
    ----------
        type:
            Always ``AuthType.OAUTH2``.
        access_token:
            The OAuth2 access token value.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[AuthType.OAUTH2] = AuthType.OAUTH2
    access_token: str


class AwsAuth(BaseModel):
    """
    AWS credential
    ===============================================
    AWS access-key credentials injected per call.
    ===============================================

    Attributes
    ----------
        type:
            Always ``AuthType.AWS``.
        access_key_id:
            The AWS access key ID.
        secret_access_key:
            The AWS secret access key.
        session_token:
            Optional temporary session token.
        region:
            Optional AWS region.
    """

    model_config = ConfigDict(frozen=True)

    type: Literal[AuthType.AWS] = AuthType.AWS
    access_key_id: str
    secret_access_key: str
    session_token: Optional[str] = None
    region: Optional[str] = None


# The per-call credential union, discriminated on ``type``.
AuthCredential = Union[
    BearerAuth,
    ApiKeyAuth,
    BasicAuth,
    OAuth2Auth,
    AwsAuth,
]


@runtime_checkable
class AuthResolver(Protocol):
    """
    Interface for resolving per-call credentials into a usable form
    ===============================================
    Normalizes an injected ``AuthCredential`` into whatever a fetcher needs
    (e.g. request headers, AWS signer inputs). Implementations are stateless
    and never store, cache, or mutate credentials on shared objects.
    ===============================================
    NOTE:
        1. ``NormalizedAuthResolver`` is the concrete implementation of this
           interface.

    Methods
    -------
        resolve_headers:
        supports:
    """

    def resolve_headers(
        self,
        credential: Optional[AuthCredential],
    ) -> dict[str, str]:
        """
        Resolve a credential into request headers

        Parameters
        ----------
            credential:
                The per-call credential, or ``None`` for unauthenticated
                access.

        Return
        ------
            headers:
                HTTP headers carrying the credential (empty when ``None``).
        """
        ...

    def supports(self, auth_type: AuthType) -> bool:
        """
        Report whether this resolver supports an auth type

        Parameters
        ----------
            auth_type:
                The canonical auth type to check.

        Return
        ------
            supported:
                ``True`` if the resolver can handle ``auth_type``.
        """
        ...


# The set of canonical auth types whose credential maps directly onto
# request headers. AWS is deliberately excluded: SigV4 signing is
# request-specific (it depends on method, path, body, and timestamp), so an
# AWS credential cannot be reduced to a static header here. AWS credential
# parts are surfaced separately via ``resolve_aws`` for a request signer.
_HEADER_AUTH_TYPES = frozenset(
    {
        AuthType.BEARER,
        AuthType.API_KEY,
        AuthType.BASIC,
        AuthType.OAUTH2,
    }
)


class NormalizedAuthResolver:
    """
    Stateless, per-call resolver for normalized credentials
    ===============================================
    The concrete ``AuthResolver``: it maps a per-call ``AuthCredential`` onto
    the HTTP headers (or AWS signer inputs) a fetcher needs. It holds no
    state, reads no ambient environment variables, loads no ``.env`` file,
    and never caches, refreshes, or mutates credentials on shared objects --
    a fresh credential is passed on every call and consumed transiently
    (see ``PHILOSOPHY.md`` section 7; decision-ledger entries #16 and #17).

    Header mapping per canonical type:

    - ``BEARER``  -> ``Authorization: Bearer <token>``
    - ``API_KEY`` -> ``<header>: <api_key>`` (header is per-credential,
      defaulting to ``X-API-Key``)
    - ``BASIC``   -> ``Authorization: Basic <base64(username:password)>``
    - ``OAUTH2``  -> ``Authorization: Bearer <access_token>``
    - ``AWS``     -> no static header; use ``resolve_aws`` to obtain the
      credential parts for a request-specific SigV4 signer.
    ===============================================
    NOTE:
        1. ``google_service_account`` is intentionally NOT a canonical
           ``AuthType``. A service-account flow is a token-exchange concern:
           the host exchanges the service-account key for a short-lived
           OAuth2 access token (token acquisition and refresh are the host's
           responsibility, per PHILOSOPHY section 7) and injects it here as
           an ``OAuth2Auth``. It therefore resolves through the OAuth2 path
           and needs no separate canonical type -- adding one would widen the
           stable auth contract for what is, on the wire, an OAuth2 bearer
           token.
        2. Resolving is a pure function of its argument. Credentials are
           frozen Pydantic models, so resolution cannot mutate them; the
           resolver additionally keeps no per-credential state of its own.

    Methods
    -------
        resolve_headers:
        resolve_aws:
        supports:
    """

    def resolve_headers(
        self,
        credential: Optional[AuthCredential],
    ) -> dict[str, str]:
        """
        Resolve a credential into request headers

        Parameters
        ----------
            credential:
                The per-call credential, or ``None`` for unauthenticated
                access. AWS credentials carry no static header and yield an
                empty mapping here (use ``resolve_aws`` instead).

        Return
        ------
            headers:
                A fresh ``dict`` of HTTP headers carrying the credential;
                empty when ``credential`` is ``None`` or is an
                ``AwsAuth``.
        """
        if credential is None:
            return {}
        if isinstance(credential, BearerAuth):
            return {"Authorization": f"Bearer {credential.token}"}
        if isinstance(credential, ApiKeyAuth):
            return {credential.header: credential.api_key}
        if isinstance(credential, BasicAuth):
            raw = f"{credential.username}:{credential.password}"
            encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        if isinstance(credential, OAuth2Auth):
            return {"Authorization": f"Bearer {credential.access_token}"}
        # AWS: signing is request-specific; no static header is produced.
        return {}

    def resolve_aws(self, credential: AwsAuth) -> dict[str, Optional[str]]:
        """
        Surface AWS credential parts for a request-specific signer

        AWS SigV4 signing depends on the concrete request (method, path,
        body, timestamp), so it cannot be reduced to a static header by a
        stateless resolver. This method instead returns the credential parts
        a caller's SigV4 signer needs, leaving the actual signing to the
        request layer.

        Parameters
        ----------
            credential:
                The per-call AWS credential.

        Return
        ------
            parts:
                A fresh mapping of ``access_key_id``, ``secret_access_key``,
                ``session_token``, and ``region`` (the optional fields may
                be ``None``).
        """
        return {
            "access_key_id": credential.access_key_id,
            "secret_access_key": credential.secret_access_key,
            "session_token": credential.session_token,
            "region": credential.region,
        }

    def supports(self, auth_type: AuthType) -> bool:
        """
        Report whether this resolver supports an auth type

        Parameters
        ----------
            auth_type:
                The canonical auth type to check.

        Return
        ------
            supported:
                ``True`` for every canonical ``AuthType`` (the header types
                plus ``AWS``, which is surfaced via ``resolve_aws``).
        """
        return auth_type in _HEADER_AUTH_TYPES or auth_type is AuthType.AWS
