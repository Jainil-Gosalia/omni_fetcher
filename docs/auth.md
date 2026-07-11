# Authentication

The v1 contract is **per-call**: credentials are passed with each
`fetch()` / `stream()` and used transiently. Connectors, the registry, and
the orchestrator never store tokens, never read environment variables or
`.env` files, and never share credential state between calls — which is what
makes one process safe for many tenants (proven by the concurrency suite in
`tests/v1/test_isolation.py`).

## Credential shapes

```python
from omni_fetcher.v1 import (
    ApiKeyAuth,
    AwsAuth,
    BasicAuth,
    BearerAuth,
    OAuth2Auth,
)

BearerAuth(token="...")                       # Authorization: Bearer <token>
ApiKeyAuth(api_key="...", header="X-API-Key") # custom header, raw value
BasicAuth(username="...", password="...")     # RFC 7617 basic auth
OAuth2Auth(access_token="...")                # host-exchanged access token
AwsAuth(access_key_id="...", secret_access_key="...")
```

Which shape each connector expects is listed in the
[connector matrix](fetchers.md). Notable pairs:

- **Jira / Confluence Cloud**: `BasicAuth(username=email, password=api_token)`;
  Server/DC personal access tokens use `BearerAuth`.
- **Slack**: `BearerAuth(token="xoxb-...")` (bot token).
- **SharePoint / Google Drive**: `OAuth2Auth(access_token=...)` — the host
  performs the OAuth exchange; OmniFetcher only carries the resulting token.

```python
from omni_fetcher.v1 import BasicAuth
from omni_fetcher.v1.connectors.jira import JiraConnector

result = await JiraConnector().fetch(
    "jira://issue/PROJ-1",
    auth=BasicAuth(username="dev@acme.io", password="api-token"),
)
```

## Multi-tenant serving

Wire one orchestrator, pass each tenant's credential per call — calls are
independent and safe to interleave:

```python
from omni_fetcher.v1 import BearerAuth, OmniFetcher, builtin_registry

omni = OmniFetcher(builtin_registry())        # shared, stateless

result_a = await omni.fetch(uri, auth=BearerAuth(token=tenant_a_token))
result_b = await omni.fetch(uri, auth=BearerAuth(token=tenant_b_token))
```

A missing or wrong-shaped credential is a typed value, not an exception:
`Error(kind=AUTH_FAILED)` (bad/missing credential) or
`Error(kind=PERMISSION_DENIED)` (authenticated but lacking scope).

## On the command line

The CLI accepts environment-variable *names*, never raw secrets, so nothing
sensitive lands in `argv` or shell history:

```bash
export JIRA_USER="dev@acme.io" JIRA_TOKEN="..."
omni-fetcher v1 fetch "jira://issue/PROJ-1" \
  --auth-type basic --username-env JIRA_USER --password-env JIRA_TOKEN
```

## Legacy `AuthConfig`

The pre-1.0 auth registry (`OmniFetcher(auth={...})`, `OMNI_*` environment
loading) still works on the legacy layer but is deprecated (removal in 2.0).
Its per-source config model is replaced by the explicit per-call credentials
above; see [migration-v1.md](migration-v1.md).
