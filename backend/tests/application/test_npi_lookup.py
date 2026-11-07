"""Reading the NPI mirror with its provenance, over in-memory storage."""

from datetime import timedelta
from uuid import uuid4

import pytest

from credentialing.application.errors import NpiNotFoundError
from credentialing.application.npi_lookup import NpiLookup
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import DatasetLoadMode, Freshness, Outcome, VerificationMethod
from credentialing.domain.models import DatasetLoad, SourceSnapshot
from credentialing.domain.policies import DEFAULT_FRESHNESS
from tests.fakes import (
    FixedClock,
    InMemoryNpiRegistry,
    InMemorySnapshots,
    ScriptedDatasetConnector,
    dataset_capabilities,
)
from tests.synthetic import NOW, make_npi, npi_person

pytestmark = pytest.mark.anyio

MONTHLY = "nppes_monthly"
NPI = make_npi("140000011")


def _lookup(snapshots: InMemorySnapshots, *, days: int = 0) -> NpiLookup:
    directory = ConnectorRegistry()
    directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
    registry = InMemoryNpiRegistry(
        npi_person(NPI), npi_person(make_npi("140000012"), first="RILEY")
    )
    clock = FixedClock(NOW + timedelta(days=days))
    return NpiLookup(registry, directory, snapshots, DEFAULT_FRESHNESS, clock)


async def _loaded(snapshots: InMemorySnapshots) -> SourceSnapshot:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=MONTHLY,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/nppes/monthly.zip",
        last_modified=NOW - timedelta(days=2),
        load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=2, added=2),
    )
    await snapshots.add(snapshot)
    return snapshot


async def test_an_npi_comes_with_the_load_the_mirror_is_as_of() -> None:
    snapshots = InMemorySnapshots()
    loaded = await _loaded(snapshots)

    view = await _lookup(snapshots, days=3).get(NPI)

    assert view.record.npi == NPI
    assert view.mirror is not None
    provenance = view.mirror.provenance
    assert (provenance.source_id, provenance.source_url) == (MONTHLY, loaded.source_url)
    assert provenance.method is VerificationMethod.BULK_DATASET
    assert provenance.verified_at == NOW
    assert provenance.source_updated_at == NOW - timedelta(days=2)
    assert provenance.snapshot_id == loaded.id
    assert view.mirror.freshness is Freshness.CACHED


async def test_an_old_mirror_reads_stale_never_live() -> None:
    snapshots = InMemorySnapshots()
    await _loaded(snapshots)

    view = await _lookup(snapshots, days=40).get(NPI)

    assert view.mirror is not None
    assert view.mirror.freshness is Freshness.STALE


async def test_an_unknown_npi_is_not_found() -> None:
    with pytest.raises(NpiNotFoundError):
        await _lookup(InMemorySnapshots()).get(make_npi("140000099"))


async def test_a_search_carries_the_mirror_and_needs_two_characters() -> None:
    snapshots = InMemorySnapshots()
    await _loaded(snapshots)
    lookup = _lookup(snapshots)

    found = await lookup.search("  Testperson Riley ", limit=10)
    short = await lookup.search("T", limit=10)

    assert found.query == "Testperson Riley"
    assert [record.npi for record in found.records] == [make_npi("140000012")]
    assert found.mirror is not None
    assert short.records == ()


async def test_a_mirror_never_loaded_has_no_provenance_to_give() -> None:
    result = await _lookup(InMemorySnapshots()).search("Testperson", limit=10)

    assert result.mirror is None
    assert len(result.records) == 2
