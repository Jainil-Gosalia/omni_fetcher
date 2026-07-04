"""Multi-tenant isolation proof for the v1 contract (issue #9, phase 5).

The statelessness/no-mutation properties are asserted in isolation elsewhere
(``test_auth.py``, ``test_registry.py``, ``test_orchestrator.py``). This
module supplies the *concurrency* proof those tests deliberately omit: the
same shared objects -- one ``NormalizedAuthResolver``, one ``FrozenRegistry``,
one ``OmniFetcher`` -- are driven from many threads and many interleaved
coroutines at once, each carrying a *distinct* tenant credential, and every
call is asserted to observe only its own auth and its own fetched data.

Why this catches a real regression: each concurrent worker uses a credential
whose token embeds a unique tenant id, and the stub fetchers echo the resolved
credential straight back into the node content. If any shared object cached,
accumulated, or mutated a per-call credential (the failure mode phase 5 rules
out), a worker would observe another tenant's token and the equality assertion
would fail. The work is spread across real OS threads (separate event loops
sharing one orchestrator) and across ``asyncio.gather`` fan-out with forced
mid-flight interleaving, over high iteration counts, so a shared-mutable-state
defect has ample opportunity to surface.
"""

from __future__ import annotations

import asyncio
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator, Optional

from omni_fetcher.v1.atoms import Text
from omni_fetcher.v1.auth import (
    AuthCredential,
    BearerAuth,
    NormalizedAuthResolver,
    OAuth2Auth,
)
from omni_fetcher.v1.fetcher import BaseFetcher
from omni_fetcher.v1.mapping import build_node
from omni_fetcher.v1.node import CompositionNode
from omni_fetcher.v1.orchestrator import OmniFetcher
from omni_fetcher.v1.registry import (
    FrozenRegistry,
    RegistryBuilder,
    SourceDefinition,
)
from omni_fetcher.v1.result import Result, Success, success

# Concurrency knobs. Large enough that interleaving is genuine and a
# shared-state leak has many chances to surface, small enough to stay fast.
_TENANTS = 64
_ITERATIONS = 40
_WORKERS = 16


def _echo_token(auth: Optional[AuthCredential]) -> str:
    """Extract the tenant-identifying token from a per-call credential."""
    if isinstance(auth, BearerAuth):
        return auth.token
    if isinstance(auth, OAuth2Auth):
        return auth.access_token
    return "anon"


def _tenant_node(token: str) -> CompositionNode:
    """A canonical node whose sole atom content is the tenant's token."""
    return build_node(kind="tenant", atoms=[Text(content=token)])


class _EchoFetcher(BaseFetcher):
    """A stub fetcher that echoes the per-call auth token into its node.

    The fetcher yields after surrendering control to the event loop, so many
    concurrent invocations are genuinely in flight at once; if the orchestrator
    or registry held per-call state, this interleaving would expose it.
    """

    async def stream(
        self,
        uri: str,
        *,
        auth: Optional[AuthCredential] = None,
        zoom=None,
    ) -> AsyncIterator[Result]:
        token = _echo_token(auth)
        # Surrender the loop mid-call so sibling coroutines interleave here.
        await asyncio.sleep(0)
        yield success(_tenant_node(token))


def _one_tenant_registry() -> FrozenRegistry:
    """A shared registry routing every tenant URI to the echo fetcher."""
    return FrozenRegistry(
        (
            SourceDefinition(
                name="echo",
                fetcher_class=_EchoFetcher,
                uri_patterns=("echo://*",),
            ),
        )
    )


# ---------------------------------------------------------------------------
# Auth resolver: one shared resolver, many threads, distinct credentials
# ---------------------------------------------------------------------------


def test_shared_resolver_never_leaks_across_threads() -> None:
    """A single resolver, hit by many threads, only ever returns own auth.

    Every thread resolves its own tenant's bearer token thousands of times
    against one shared ``NormalizedAuthResolver``, starting simultaneously via
    a barrier to maximise contention. Any cross-tenant header would mean the
    resolver carried per-call state -- the exact leak phase 5 forbids.
    """
    resolver = NormalizedAuthResolver()
    barrier = threading.Barrier(_WORKERS)
    tokens = [f"tenant-{i}" for i in range(_WORKERS)]
    leaks: list[tuple[str, dict]] = []

    def hammer(token: str) -> None:
        cred = BearerAuth(token=token)
        expected = {"Authorization": f"Bearer {token}"}
        barrier.wait()
        for _ in range(_ITERATIONS * 25):
            headers = resolver.resolve_headers(cred)
            if headers != expected:
                leaks.append((token, headers))

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(hammer, tokens))

    assert leaks == []


def test_resolver_holds_no_per_call_instance_state() -> None:
    """Resolving many distinct credentials adds no attributes to the resolver.

    The resolver is stateless: it must never stash a resolved credential or
    header on ``self``. After a concurrent barrage its instance ``__dict__``
    stays empty.
    """
    resolver = NormalizedAuthResolver()

    def resolve(i: int) -> None:
        resolver.resolve_headers(BearerAuth(token=f"t-{i}"))
        resolver.resolve_headers(OAuth2Auth(access_token=f"oauth-{i}"))

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(resolve, range(_TENANTS)))

    # No credential/header cached onto the shared instance.
    assert getattr(resolver, "__dict__", {}) == {}


def test_oauth2_tokens_not_cached_onto_shared_objects() -> None:
    """Concurrent OAuth2 resolution never mutates or caches tokens.

    Each worker resolves a distinct OAuth2 access token against one shared
    resolver. Every resolution must reflect only that worker's token, the
    frozen credential must be unchanged afterwards, and tampering with a
    returned header dict must never corrupt a subsequent resolution (proving a
    fresh dict is handed out, never a shared mutable one).
    """
    resolver = NormalizedAuthResolver()
    barrier = threading.Barrier(_WORKERS)
    mismatches: list[str] = []
    lock = threading.Lock()

    def resolve(i: int) -> None:
        cred = OAuth2Auth(access_token=f"ya29.tenant-{i}")
        expected = {"Authorization": f"Bearer ya29.tenant-{i}"}
        barrier.wait()
        for _ in range(_ITERATIONS):
            headers = resolver.resolve_headers(cred)
            if headers != expected:
                with lock:
                    mismatches.append(f"{i}: {headers}")
            # Tamper with our copy; a fresh dict per call means the next
            # resolution (ours or another thread's) is unaffected.
            headers["Authorization"] = "TAMPERED"
            again = resolver.resolve_headers(cred)
            if again != expected:
                with lock:
                    mismatches.append(f"{i} after tamper: {again}")
        # Frozen credential is unchanged by any amount of resolution.
        assert cred.access_token == f"ya29.tenant-{i}"

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(resolve, range(_WORKERS)))

    assert mismatches == []


# ---------------------------------------------------------------------------
# Registry: one shared immutable registry, concurrent routing
# ---------------------------------------------------------------------------


def test_shared_registry_routes_correctly_under_concurrency() -> None:
    """Many threads route distinct URIs through one shared registry.

    A single ``FrozenRegistry`` with one distinct fetcher class per tenant is
    resolved concurrently; each thread must always get back exactly its own
    fetcher class. Immutable shared state routes deterministically with no
    corruption.
    """
    # A distinct fetcher subclass per tenant so routing is checkable by class.
    classes = {
        i: type(f"Fetcher{i}", (_EchoFetcher,), {}) for i in range(_TENANTS)
    }
    registry = FrozenRegistry(
        tuple(
            SourceDefinition(
                name=f"src-{i}",
                fetcher_class=classes[i],
                uri_patterns=(f"src{i}://*",),
            )
            for i in range(_TENANTS)
        )
    )
    barrier = threading.Barrier(_WORKERS)
    misroutes: list[str] = []

    def route(worker: int) -> None:
        rng = random.Random(worker)
        barrier.wait()
        for _ in range(_ITERATIONS * 10):
            i = rng.randrange(_TENANTS)
            resolved = registry.resolve(f"src{i}://x")
            if resolved is not classes[i]:
                misroutes.append(f"src{i} -> {resolved}")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(route, range(_WORKERS)))

    assert misroutes == []


def test_shared_registry_stays_immutable_under_concurrency() -> None:
    """The shared registry rejects mutation from every thread that tries.

    Read-only after build must hold under contention: concurrent attempts to
    rebind the registry's internals all raise, and its definition set is
    unchanged afterwards.
    """
    registry = _one_tenant_registry()
    barrier = threading.Barrier(_WORKERS)
    rejections = 0
    lock = threading.Lock()

    def attack(_: int) -> None:
        nonlocal rejections
        barrier.wait()
        for _ in range(_ITERATIONS):
            try:
                registry._definitions = ()  # type: ignore[misc]
            except AttributeError:
                with lock:
                    rejections += 1

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(attack, range(_WORKERS)))

    assert rejections == _WORKERS * _ITERATIONS
    assert len(registry.definitions()) == 1


def test_concurrent_builders_do_not_share_state() -> None:
    """Independent ``RegistryBuilder``s built in parallel never cross-pollute.

    Proves there is no process-global mutable registry: each thread builds its
    own registry with its own single source and must see only that source.
    """
    results: dict[int, list[str]] = {}
    lock = threading.Lock()

    def build(i: int) -> None:
        builder = RegistryBuilder()
        builder.add(
            SourceDefinition(
                name=f"only-{i}",
                fetcher_class=_EchoFetcher,
                uri_patterns=(f"only{i}://*",),
            )
        )
        registry = builder.build()
        with lock:
            results[i] = [d.name for d in registry.definitions()]

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(build, range(_TENANTS)))

    assert results == {i: [f"only-{i}"] for i in range(_TENANTS)}


# ---------------------------------------------------------------------------
# Orchestrator: one shared orchestrator, concurrent tenant fetches
# ---------------------------------------------------------------------------


async def test_gathered_fetches_are_isolated_by_tenant() -> None:
    """Concurrent ``fetch()`` calls each return only their own tenant's data.

    One shared ``OmniFetcher`` is fanned out over ``asyncio.gather`` with a
    distinct bearer token per call. Because the stub yields mid-call, all
    coroutines interleave; each result's echoed content must still equal its
    own token.
    """
    omni = OmniFetcher(_one_tenant_registry())
    tokens = [f"tenant-{i}" for i in range(_TENANTS)]

    async def fetch(token: str) -> Result:
        return await omni.fetch("echo://x", auth=BearerAuth(token=token))

    results = await asyncio.gather(*(fetch(t) for t in tokens))

    for token, result in zip(tokens, results):
        assert isinstance(result, Success)
        assert [a.content for a in result.tree.iter_atoms()] == [token]


async def test_repeated_gather_rounds_never_contaminate() -> None:
    """Many interleaved rounds of concurrent fetches stay tenant-isolated.

    Repeats the gather fan-out over many rounds with shuffled scheduling so a
    latent shared-state leak has repeated opportunity to surface; every call
    in every round observes only its own token.
    """
    omni = OmniFetcher(_one_tenant_registry())

    async def fetch(token: str) -> tuple[str, Result]:
        await asyncio.sleep(random.uniform(0, 0.001))
        result = await omni.fetch("echo://x", auth=BearerAuth(token=token))
        return token, result

    for round_no in range(_ITERATIONS):
        tokens = [f"r{round_no}-t{i}" for i in range(_TENANTS)]
        pairs = await asyncio.gather(*(fetch(t) for t in tokens))
        for token, result in pairs:
            assert isinstance(result, Success)
            assert [a.content for a in result.tree.iter_atoms()] == [token]


def test_threaded_fetches_share_one_orchestrator_without_leaking() -> None:
    """Real OS threads sharing one orchestrator stay tenant-isolated.

    Each thread drives its own event loop against the *same* ``OmniFetcher``
    (and its shared immutable registry), fetching with a distinct tenant
    token many times. Every returned tree must echo only that thread's token.
    """
    omni = OmniFetcher(_one_tenant_registry())
    barrier = threading.Barrier(_WORKERS)
    leaks: list[str] = []
    lock = threading.Lock()

    async def _fetch(token: str) -> Result:
        return await omni.fetch("echo://x", auth=BearerAuth(token=token))

    def worker(worker_id: int) -> None:
        token = f"worker-{worker_id}"
        barrier.wait()
        for _ in range(_ITERATIONS):
            result = asyncio.run(_fetch(token))
            contents = (
                [a.content for a in result.tree.iter_atoms()]
                if isinstance(result, Success)
                else [f"<{type(result).__name__}>"]
            )
            if contents != [token]:
                with lock:
                    leaks.append(f"{token}: {contents}")

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(worker, range(_WORKERS)))

    assert leaks == []


def test_orchestrator_retains_no_state_after_concurrent_calls() -> None:
    """The shared orchestrator holds no per-call data after a concurrent run.

    ``OmniFetcher`` declares ``__slots__``; after a burst of concurrent
    tenant fetches its only slots must still be the same registry and resolver
    it was wired with -- no fetched tree, credential, or routing result stuck
    to the instance.
    """
    resolver = NormalizedAuthResolver()
    registry = _one_tenant_registry()
    omni = OmniFetcher(registry, auth_resolver=resolver)

    def worker(i: int) -> None:
        asyncio.run(
            omni.fetch("echo://x", auth=BearerAuth(token=f"t-{i}"))
        )

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        list(pool.map(worker, range(_TENANTS)))

    # Slots are exactly the wired dependencies; nothing accumulated.
    assert omni._registry is registry
    assert omni._auth_resolver is resolver
    # __slots__ means no instance __dict__ exists to hold stray per-call state.
    assert not hasattr(omni, "__dict__")


async def test_shared_resolver_in_concurrent_fetch_path_is_isolated() -> None:
    """A resolver shared *inside* the concurrent fetch path never leaks.

    Wires a stub fetcher that resolves auth through one shared
    ``NormalizedAuthResolver`` (closed over) mid-stream and echoes the
    resulting header. Under interleaved gather fan-out, each call's echoed
    header must carry only its own tenant token -- proving the shared resolver
    is safe on the hot path, not just in isolation.
    """
    resolver = NormalizedAuthResolver()

    class _ResolvingFetcher(BaseFetcher):
        async def stream(
            self,
            uri: str,
            *,
            auth: Optional[AuthCredential] = None,
            zoom=None,
        ) -> AsyncIterator[Result]:
            await asyncio.sleep(0)
            headers = resolver.resolve_headers(auth)
            await asyncio.sleep(0)
            yield success(
                build_node(
                    kind="tenant",
                    atoms=[Text(content=headers.get("Authorization", ""))],
                )
            )

    registry = FrozenRegistry(
        (
            SourceDefinition(
                name="resolving",
                fetcher_class=_ResolvingFetcher,
                uri_patterns=("echo://*",),
            ),
        )
    )
    omni = OmniFetcher(registry, auth_resolver=resolver)
    tokens = [f"tenant-{i}" for i in range(_TENANTS)]

    async def fetch(token: str) -> Result:
        return await omni.fetch("echo://x", auth=OAuth2Auth(access_token=token))

    results = await asyncio.gather(*(fetch(t) for t in tokens))

    for token, result in zip(tokens, results):
        assert isinstance(result, Success)
        assert [a.content for a in result.tree.iter_atoms()] == [
            f"Bearer {token}"
        ]
