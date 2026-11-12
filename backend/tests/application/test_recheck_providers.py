"""Rechecking the monitored rosters over in-memory storage, and the syncs that trigger it."""

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest

from credentialing.application.check_exclusions import CheckExclusions, ListState
from credentialing.application.errors import UnknownSourceError
from credentialing.application.npi_lookup import NpiLookup
from credentialing.application.provider_status import ProviderStatusKeeper
from credentialing.application.recheck_providers import (
    NO_PROVIDERS,
    NONE_GIVEN,
    NOTHING_DUE,
    NPI_SET_TRIGGER,
    RecheckProviders,
)
from credentialing.application.register_provider import ProviderInput, RegisterProvider
from credentialing.application.sync_source import SyncSource
from credentialing.application.verify_npi import VerifyNpi
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.domain.enums import (
    Attention,
    CredentialType,
    DatasetLoadMode,
    EntityKind,
    Finding,
    Freshness,
    Outcome,
)
from credentialing.domain.models import (
    DatasetLoad,
    ExclusionRecord,
    FetchResult,
    PersonName,
    Provider,
    SourceSnapshot,
)
from credentialing.domain.policies import (
    DEFAULT_FRESHNESS,
    HISTORY_RETENTION,
    MONITORING_INTERVAL,
)
from credentialing.domain.ports import DatasetFetch
from credentialing.domain.types import Npi
from tests.fakes import (
    API_SOURCE,
    FakeRechecker,
    FixedClock,
    InMemoryMonitoringRuns,
    InMemoryNpiRegistry,
    InMemoryProviders,
    InMemoryRecords,
    InMemorySnapshots,
    InMemoryVerifications,
    ListDataset,
    ScriptedConnector,
    ScriptedDatasetConnector,
    ScriptedNpiRegistryConnector,
    api_no_result,
    capabilities,
    dataset_capabilities,
)
from tests.synthetic import NOW, exclusion, individual, make_npi, npi_person

pytestmark = pytest.mark.anyio

FEDERAL, STATE, MONTHLY = "fake_federal", "fake_state", "fake_monthly"
NPI = make_npi("170000001")
ORG_A, ORG_B = UUID(int=0xA), UUID(int=0xB)


class _FailingRecords(InMemoryRecords):
    """Candidate search that fails for one made-up surname, as a broken row would."""

    async def find_candidates(
        self,
        npi: Npi | None,
        last_name: str | None,
        organization_name: str | None,
        *,
        license_numbers: Sequence[str] = (),
    ) -> Sequence[ExclusionRecord]:
        if last_name == "Failperson":
            raise RuntimeError("synthetic failure for Failperson")
        return await super().find_candidates(
            npi, last_name, organization_name, license_numbers=license_numbers
        )


class _FailingPurge(InMemoryVerifications):
    """A purge that fails for organization A, as a database error would."""

    async def purge(self, organization_id: UUID, *, before: datetime) -> int:
        if organization_id == ORG_A:
            raise RuntimeError("synthetic purge failure")
        return await super().purge(organization_id, before=before)


class _World:
    """Two lists, a stale NPI mirror, a live registry that must never be asked, and rosters."""

    def __init__(
        self,
        *providers: Provider,
        organizations: Sequence[UUID] = (),
        verifications: InMemoryVerifications | None = None,
    ) -> None:
        self.clock = FixedClock(NOW)
        self.directory = ConnectorRegistry()
        self.directory.register(ScriptedConnector(capabilities(FEDERAL)))
        self.directory.register(ScriptedConnector(capabilities(STATE, frozenset({"NY"}))))
        self.directory.register(ScriptedDatasetConnector(dataset_capabilities(MONTHLY)))
        self.live = ScriptedNpiRegistryConnector(api_no_result(fetched_at=NOW))
        self.directory.register(self.live)
        self.snapshots = InMemorySnapshots()
        self.records = _FailingRecords()
        self.providers = InMemoryProviders(*providers, organizations=organizations)
        self.verifications = verifications or InMemoryVerifications()
        self.runs = InMemoryMonitoringRuns()
        self.keeper = ProviderStatusKeeper(self.providers, self.verifications, self.directory)
        lookup = NpiLookup(
            InMemoryNpiRegistry(npi_person(NPI)),
            self.directory,
            self.snapshots,
            DEFAULT_FRESHNESS,
            self.clock,
        )
        self.check = CheckExclusions(
            self.directory,
            self.providers,
            self.records,
            self.snapshots,
            self.verifications,
            DEFAULT_FRESHNESS,
            self.clock,
            status=self.keeper,
        )
        verify = VerifyNpi(
            self.providers,
            lookup,
            self.verifications,
            self.clock,
            source_id=API_SOURCE,
            source_url="https://example.test/registry",
            status=self.keeper,
        )
        self.recheck = RecheckProviders(
            self.providers,
            self.check,
            verify,
            self.keeper,
            self.runs,
            self.clock,
            verifications=self.verifications,
        )
        # The mirror was loaded long ago: an ordinary lookup would ask the live registry.
        self.snapshots.stored.append(
            SourceSnapshot(
                id=uuid4(),
                source_id=MONTHLY,
                fetched_at=NOW - timedelta(days=60),
                outcome=Outcome.COMPLETED,
                source_url="https://example.test/nppes/monthly.zip",
                load=DatasetLoad(mode=DatasetLoadMode.FULL, scanned=1, added=1),
            )
        )

    async def load(self, source_id: str, *records: ExclusionRecord) -> None:
        snapshot = SourceSnapshot(
            id=uuid4(),
            source_id=source_id,
            fetched_at=self.clock.now() - timedelta(hours=1),
            outcome=Outcome.COMPLETED,
            source_url=f"https://example.test/{source_id}",
            content_hash=uuid4().hex,
        )
        await self.snapshots.add(snapshot)
        own = tuple(r.model_copy(update={"snapshot_id": snapshot.id}) for r in records)
        await self.records.replace_current(
            source_id, snapshot.id, own, snapshot.fetched_at, source_changed=True
        )

    def results(self, provider: Provider) -> list[tuple[str, CredentialType]]:
        return sorted(
            (r.source_id, r.credential_type)
            for r in self.verifications.stored
            if r.provider_id == provider.id
        )


def _a(last: str, npi: str | None = None) -> Provider:
    return individual(last=last, npi=npi, organization_id=ORG_A)


def _b(last: str, npi: str | None = None) -> Provider:
    return individual(last=last, npi=npi, organization_id=ORG_B)


async def test_a_full_recheck_checks_every_organization_under_its_own_id() -> None:
    first, second, other = _a("Alphaperson", NPI), _a("Betaperson"), _b("Gammaperson", NPI)
    world = _World(first, second, other)
    await world.load(FEDERAL)
    await world.load(STATE)

    run = await world.recheck.run(trigger="cli")

    assert (run.organizations, run.providers, run.results, run.failures) == (2, 3, 8, 0)
    assert (run.trigger, run.source_id, run.changed, run.note) == ("cli", None, 0, None)
    assert world.results(first) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (MONTHLY, CredentialType.NPI),
        (STATE, CredentialType.EXCLUSION),
    ]
    assert world.results(second) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (STATE, CredentialType.EXCLUSION),
    ]
    for result in world.verifications.stored:
        owner = await world.providers.get(result.organization_id, result.provider_id)
        assert owner is not None
    assert world.runs.stored == [run]
    assert set(run.phases) == {"lists", "providers", "exclusions", "npi", "purge"}
    assert run.cpu_time is not None


async def test_a_first_check_checks_the_given_providers_of_one_organization_only() -> None:
    new, old, other = _a("Alphaperson", NPI), _a("Betaperson"), _b("Gammaperson", NPI)
    world = _World(new, old, other)
    await world.load(FEDERAL)
    await world.load(STATE)

    run = await world.recheck.run_for(ORG_A, [new.id, other.id])
    nothing = await world.recheck.run_for(ORG_B, [new.id])

    assert (run.trigger, run.organizations, run.providers, run.results) == ("import", 1, 1, 3)
    assert (run.failures, run.note, run.source_id) == (0, None, None)
    assert world.results(new) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (MONTHLY, CredentialType.NPI),
        (STATE, CredentialType.EXCLUSION),
    ]
    assert world.results(old) == []
    assert world.results(other) == []
    assert (nothing.providers, nothing.organizations, nothing.note) == (0, 0, NONE_GIVEN)
    assert world.live.lookups == []
    assert world.runs.stored == [run, nothing]
    # Every provider of the organization has a stored status, checked or not.
    assert all(status is not None for _, status in await world.providers.list_all(ORG_A))


async def test_adding_one_provider_runs_its_first_check_in_its_organization() -> None:
    other = _b("Gammaperson", NPI)
    world = _World(other)
    await world.load(FEDERAL)
    await world.load(STATE)
    register = RegisterProvider(world.providers, world.clock, first_check=world.recheck)
    data = ProviderInput(
        entity_kind=EntityKind.INDIVIDUAL,
        person_name=PersonName(last="Alphaperson", first="Avery"),
        npi=NPI,
    )

    provider = await register.run(ORG_A, data)

    (run,) = world.runs.stored
    assert (run.trigger, run.organizations, run.providers, run.results) == ("add", 1, 1, 3)
    assert world.results(provider) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (MONTHLY, CredentialType.NPI),
        (STATE, CredentialType.EXCLUSION),
    ]
    assert world.results(other) == []
    assert world.live.lookups == []
    ((_, status),) = await world.providers.list_all(ORG_A)
    assert status is not None
    assert status.last_checked_at is not None


async def test_a_recheck_after_an_npi_is_set_checks_the_lists_only() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(FEDERAL)
    await world.load(STATE)

    run = await world.recheck.run_for(ORG_A, [provider.id], trigger=NPI_SET_TRIGGER, npi=False)

    assert (run.trigger, run.organizations, run.providers, run.results) == ("npi_set", 1, 1, 2)
    assert (run.failures, run.note) == (0, None)
    assert world.results(provider) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (STATE, CredentialType.EXCLUSION),
    ]
    assert set(run.phases) == {"lists", "providers", "exclusions"}
    assert world.live.lookups == []


async def test_a_first_check_counts_a_provider_whose_check_fails() -> None:
    failing, fine = _a("Failperson"), _a("Alphaperson")
    world = _World(failing, fine)
    await world.load(FEDERAL)

    run = await world.recheck.run_for(ORG_A, [failing.id, fine.id])

    assert (run.providers, run.failures, run.results) == (2, 1, 2)


async def test_the_npi_is_verified_from_the_mirror_only_even_when_it_is_stale() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)

    await world.recheck.run(trigger="due", due_only=True)

    (result,) = [r for r in world.verifications.stored if r.credential_type is CredentialType.NPI]
    assert world.live.lookups == []
    assert (result.source_id, result.freshness) == (MONTHLY, Freshness.STALE)
    assert result.details["npi_status"] == "active"


async def test_statuses_follow_every_recheck() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(FEDERAL)
    await world.load(STATE)

    await world.recheck.run(trigger="cli")

    ((_, status),) = await world.providers.list_all(ORG_A)
    assert status is not None
    assert status.attention is None
    assert status.next_check_at == NOW + MONITORING_INTERVAL


async def test_due_only_skips_providers_rechecked_within_the_interval() -> None:
    world = _World(_a("Alphaperson", NPI), _b("Gammaperson"))
    await world.load(FEDERAL)
    await world.load(STATE)
    await world.recheck.run(trigger="due", due_only=True)

    world.clock.current = NOW + timedelta(days=1)
    fresh = await world.recheck.run(trigger="due", due_only=True)
    world.clock.current = NOW + MONITORING_INTERVAL
    due = await world.recheck.run(trigger="due", due_only=True)

    assert (fresh.providers, fresh.results, fresh.organizations, fresh.note) == (
        0,
        0,
        0,
        NOTHING_DUE,
    )
    assert (due.providers, due.organizations) == (2, 2)


async def test_a_list_recheck_checks_that_list_only_and_counts_what_changed() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(FEDERAL)
    await world.load(STATE)
    await world.recheck.run(trigger="cli")
    world.clock.current = NOW + timedelta(days=2)
    await world.load(STATE, exclusion(source_id=STATE, last="Alphaperson", npi=NPI))

    run = await world.recheck.run(trigger="list_sync:fake_state", source_id=STATE)

    assert (run.providers, run.results, run.changed, run.source_id) == (1, 1, 1, STATE)
    latest = world.verifications.stored[-1]
    assert (latest.source_id, latest.finding) == (STATE, Finding.RECORD_FOUND)
    assert latest.changed_from is not None
    assert latest.changed_from.finding is Finding.NO_RECORD
    ((_, status),) = await world.providers.list_all(ORG_A)
    assert status is not None
    assert (status.attention, status.changed) == (Attention.LISTED, True)
    # The full recheck stays due 15 days after the full check, not after the list's.
    assert status.next_check_at == NOW + MONITORING_INTERVAL


async def test_an_unchanged_recheck_appends_results_without_changes() -> None:
    provider = _a("Alphaperson")
    world = _World(provider)
    await world.load(FEDERAL)
    await world.load(STATE)
    await world.recheck.run(trigger="cli")
    world.clock.current = NOW + timedelta(days=15)

    run = await world.recheck.run(trigger="cli")

    assert (run.results, run.changed) == (2, 0)
    assert len(world.verifications.stored) == 4
    assert all(r.changed_from is None for r in world.verifications.stored)


async def test_a_failing_provider_is_counted_logged_without_names_and_the_run_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    failing, fine = _a("Failperson"), _b("Gammaperson")
    world = _World(failing, fine)
    await world.load(FEDERAL)

    with caplog.at_level(logging.WARNING):
        run = await world.recheck.run(trigger="cli")

    assert (run.failures, run.providers, run.results) == (1, 2, 2)
    assert world.results(fine) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (STATE, CredentialType.EXCLUSION),
    ]
    assert world.results(failing) == []
    assert f"organization_id={ORG_A}" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "Failperson" not in caplog.text


async def test_npi_only_reverifies_providers_with_an_npi() -> None:
    with_npi, without = _a("Alphaperson", NPI), _a("Betaperson")
    world = _World(with_npi, without)

    run = await world.recheck.run(trigger="nppes_load", npi_only=True)

    assert (run.providers, run.results) == (2, 1)
    assert world.results(with_npi) == [(MONTHLY, CredentialType.NPI)]
    assert world.results(without) == []


async def test_a_run_without_providers_is_stored_with_a_note() -> None:
    world = _World(organizations=[ORG_A])

    run = await world.recheck.run(trigger="due", due_only=True)

    assert (run.organizations, run.providers, run.note) == (0, 0, NO_PROVIDERS)
    assert world.runs.stored == [run]


async def test_an_unknown_list_is_refused_before_anything_is_checked() -> None:
    world = _World(_a("Alphaperson"))

    with pytest.raises(UnknownSourceError):
        await world.recheck.run(trigger="cli", source_id="no_such_list")

    assert world.verifications.stored == []
    assert world.runs.stored == []


# --- The syncs that trigger a recheck -------------------------------------------------------


def _list_sync(rechecker: FakeRechecker, *results: FetchResult) -> SyncSource:
    directory = ConnectorRegistry()
    directory.register(ScriptedConnector(capabilities(STATE), *results))
    return SyncSource(
        directory, InMemorySnapshots(), InMemoryRecords(), FixedClock(NOW), recheck=rechecker
    )


def _fetched(*lasts: str, hours: int = 0) -> FetchResult:
    snapshot = SourceSnapshot(
        id=uuid4(),
        source_id=STATE,
        fetched_at=NOW + timedelta(hours=hours),
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/list",
        content_hash=uuid4().hex,
    )
    records = tuple(exclusion(source_id=STATE, snapshot_id=snapshot.id, last=n) for n in lasts)
    return FetchResult(snapshot=snapshot, records=records)


async def test_a_list_sync_that_changed_rows_rechecks_the_rosters_against_that_list() -> None:
    rechecker = FakeRechecker()
    sync = _list_sync(rechecker, _fetched("Testperson"), _fetched("Testperson", hours=1))

    first = await sync.run(STATE)
    unchanged = await sync.run(STATE)

    assert rechecker.calls == [
        {
            "trigger": "list_sync:fake_state",
            "source_id": STATE,
            "due_only": False,
            "npi_only": False,
        }
    ]
    assert first.recheck is not None
    assert first.recheck.source_id == STATE
    assert (unchanged.recheck, unchanged.recheck_failed) == (None, None)


async def test_a_recheck_failure_is_reported_and_the_sync_stands() -> None:
    rechecker = FakeRechecker(fail=RuntimeError("synthetic recheck failure"))
    sync = _list_sync(rechecker, _fetched("Testperson"))

    report = await sync.run(STATE)

    assert report.recheck is None
    assert report.recheck_failed == "roster recheck failed (RuntimeError)"
    assert report.diff is not None
    assert report.diff.added == 1


async def test_a_dataset_load_that_changed_rows_reverifies_npis_only() -> None:
    rechecker = FakeRechecker()
    directory = ConnectorRegistry()
    first = SourceSnapshot(
        id=uuid4(),
        source_id=MONTHLY,
        fetched_at=NOW,
        outcome=Outcome.COMPLETED,
        source_url="https://example.test/nppes",
        content_hash=uuid4().hex,
    )
    directory.register(
        ScriptedDatasetConnector(
            dataset_capabilities(MONTHLY),
            DatasetFetch(
                snapshot=first, mode=DatasetLoadMode.FULL, dataset=ListDataset(npi_person(NPI))
            ),
        )
    )
    sync = SyncSource(
        directory,
        InMemorySnapshots(),
        InMemoryRecords(),
        FixedClock(NOW),
        npi_registry=InMemoryNpiRegistry(),
        recheck=rechecker,
    )

    report = await sync.run(MONTHLY)

    assert rechecker.calls == [
        {"trigger": "nppes_load", "source_id": None, "due_only": False, "npi_only": True}
    ]
    assert report.recheck is not None


async def test_a_newly_loaded_list_does_not_mark_providers_changed() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(FEDERAL)
    await world.recheck.run(trigger="cli")  # the state list was never loaded: could not check
    world.clock.current = NOW + timedelta(days=1)
    await world.load(STATE)

    run = await world.recheck.run(trigger="list_sync:fake_state", source_id=STATE)

    latest = world.verifications.stored[-1]
    assert (latest.source_id, latest.outcome) == (STATE, Outcome.COMPLETED)
    assert (run.results, run.changed) == (1, 0)
    assert latest.changed_from is None
    ((_, status),) = await world.providers.list_all(ORG_A)
    assert status is not None
    assert (status.changed, status.attention) == (False, None)


async def test_a_change_across_a_gap_of_could_not_check_results_is_caught() -> None:
    """No record, then the list could not be read, then a record found."""
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(STATE)
    first = await world.check.check(provider, await world.check.lists(STATE))
    world.clock.current = NOW + timedelta(days=15)
    gap = await world.check.check(provider, (await _unreadable(world, STATE),))
    world.clock.current = NOW + timedelta(days=30)
    await world.load(STATE, exclusion(source_id=STATE, last="Alphaperson", npi=NPI))

    found = await world.check.check(provider, await world.check.lists(STATE))

    assert [r.outcome for r in (*first, *gap)] == [Outcome.COMPLETED, Outcome.SOURCE_UNAVAILABLE]
    assert gap[0].changed_from is None
    (result,) = found
    assert result.finding is Finding.RECORD_FOUND
    assert result.changed_from is not None
    assert result.changed_from.finding is Finding.NO_RECORD
    assert result.changed_from.verified_at == first[0].provenance.verified_at


async def _unreadable(world: _World, source_id: str) -> ListState:
    """The list as a check sees it when it has no completed fetch: could not check."""
    (state,) = await world.check.lists(source_id)
    return ListState(capabilities=state.capabilities, latest=None, completed=None)


# --- History retention ---------------------------------------------------------------------

LATER = NOW + HISTORY_RETENTION + timedelta(days=17)


@pytest.mark.parametrize(("trigger", "due_only"), [("due", True), ("cli", False)])
async def test_due_and_cli_runs_purge_history_older_than_the_retention_window(
    trigger: str, due_only: bool
) -> None:
    first, other = _a("Alphaperson", NPI), _b("Gammaperson")
    world = _World(first, other)
    await world.load(FEDERAL)
    await world.load(STATE)
    await world.recheck.run(trigger="cli")
    world.clock.current = LATER
    await world.load(FEDERAL)
    await world.load(STATE)

    run = await world.recheck.run(trigger=trigger, due_only=due_only)

    # The first check's results (3 for A, 2 for B) are older than six months, and newer ones
    # of every source exist now.
    assert (run.providers, run.results, run.purged, run.failures) == (2, 5, 5, 0)
    assert "purge" in run.phases
    assert {r.created_at for r in world.verifications.stored} == {LATER}


async def test_a_purge_keeps_each_sources_newest_result_however_old() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(FEDERAL)
    await world.load(STATE)
    await world.recheck.run(trigger="cli")
    world.clock.current = LATER
    await world.load(STATE)

    run = await world.recheck.run(trigger="cli", source_id=STATE)

    # Only the state list was rechecked: its old result goes, the federal list's and the NPI's
    # stay, since they are still the newest of their source.
    assert (run.results, run.purged) == (1, 1)
    assert world.results(provider) == [
        (FEDERAL, CredentialType.EXCLUSION),
        (MONTHLY, CredentialType.NPI),
        (STATE, CredentialType.EXCLUSION),
    ]


async def test_other_runs_never_purge() -> None:
    provider = _a("Alphaperson", NPI)
    world = _World(provider)
    await world.load(FEDERAL)
    await world.load(STATE)
    await world.recheck.run(trigger="cli")
    world.clock.current = LATER
    await world.load(STATE)
    await world.load(FEDERAL)

    runs = [
        await world.recheck.run(trigger="list_sync:fake_state", source_id=STATE),
        await world.recheck.run(trigger="nppes_load", npi_only=True),
        await world.recheck.run_for(ORG_A, [provider.id]),
        await world.recheck.run_for(ORG_A, [provider.id], trigger="add"),
        await world.recheck.run_for(ORG_A, [provider.id], trigger=NPI_SET_TRIGGER, npi=False),
    ]

    assert [run.purged for run in runs] == [0] * len(runs)
    assert all("purge" not in run.phases for run in runs)
    assert any(r.created_at == NOW for r in world.verifications.stored)


async def test_a_failing_purge_is_counted_logged_without_names_and_the_run_goes_on(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mine, other = _a("Alphaperson"), _b("Gammaperson")
    world = _World(mine, other, verifications=_FailingPurge())
    await world.load(FEDERAL)
    await world.recheck.run(trigger="cli")
    world.clock.current = LATER
    await world.load(FEDERAL)

    with caplog.at_level(logging.WARNING):
        run = await world.recheck.run(trigger="cli")

    assert (run.providers, run.failures, run.purged) == (2, 1, 2)
    assert f"history purge failed organization_id={ORG_A}" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "Alphaperson" not in caplog.text
