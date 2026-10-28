"""Wiring of concrete implementations, written once for the API (``main``) and the CLI.

Builds the settings-driven resources (database pool, the shared HTTP client, clock), the
connector registry, the repositories and the use cases.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

import httpx2
from fastapi import FastAPI

from credentialing.api.app import create_app
from credentialing.api.container import AppContainer
from credentialing.application.check_exclusions import CheckExclusions
from credentialing.application.list_providers import ListProviders
from credentialing.application.lookup_exclusions import LookupExclusions
from credentialing.application.provider_verdict import ProviderVerdictQuery
from credentialing.application.register_provider import RegisterProvider
from credentialing.application.source_status import SourceStatusReport
from credentialing.application.sync_source import SyncSource
from credentialing.application.verification_history import VerificationHistory
from credentialing.connectors.framework.http import Fetcher, HttpFetcher
from credentialing.connectors.ia_medicaid import IaMedicaidConnector
from credentialing.connectors.in_fssa import InFssaConnector
from credentialing.connectors.md_medicaid import MdMedicaidConnector
from credentialing.connectors.nc_medicaid import NcMedicaidConnector
from credentialing.connectors.nd_medicaid import NdMedicaidConnector
from credentialing.connectors.ny_omig import NyOmigConnector
from credentialing.connectors.oig_leie import OigLeieConnector
from credentialing.connectors.pa_medicheck import PaMedicheckConnector
from credentialing.connectors.registry import ConnectorRegistry
from credentialing.connectors.vt_medicaid import VtMedicaidConnector
from credentialing.connectors.wa_hca import WaHcaConnector
from credentialing.domain.policies import DEFAULT_FRESHNESS
from credentialing.domain.ports import Clock
from credentialing.infrastructure.clock import SystemClock
from credentialing.infrastructure.config import Settings
from credentialing.infrastructure.database import ConnectionSource, create_pool
from credentialing.infrastructure.logging import configure_logging
from credentialing.infrastructure.repositories.exclusion_records import (
    PostgresExclusionRecordRepository,
)
from credentialing.infrastructure.repositories.providers import PostgresProviderRepository
from credentialing.infrastructure.repositories.snapshots import PostgresSnapshotRepository
from credentialing.infrastructure.repositories.verifications import (
    PostgresVerificationRepository,
)

_ACCEPT: Final = (
    "text/csv, text/tab-separated-values, "
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, text/plain;q=0.9, "
    "*/*;q=0.1"
)
_ACCEPT_LANGUAGE: Final = "en-US,en;q=0.9"


@dataclass(frozen=True, slots=True)
class Services:
    """Every repository and use case, built over one database connection source."""

    registry: ConnectorRegistry
    clock: Clock
    providers: PostgresProviderRepository
    snapshots: PostgresSnapshotRepository
    records: PostgresExclusionRecordRepository
    verifications: PostgresVerificationRepository
    sync_source: SyncSource
    source_status: SourceStatusReport
    check_exclusions: CheckExclusions
    lookup_exclusions: LookupExclusions
    register_provider: RegisterProvider
    list_providers: ListProviders
    verification_history: VerificationHistory
    provider_verdict: ProviderVerdictQuery


def create_http_client(settings: Settings) -> httpx2.AsyncClient:
    """The one HTTP client for every source: timeout, redirects and the configured identity."""
    return httpx2.AsyncClient(
        timeout=settings.http_timeout_seconds,
        follow_redirects=True,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": _ACCEPT,
            "Accept-Language": _ACCEPT_LANGUAGE,
        },
    )


def build_registry(fetcher: Fetcher) -> ConnectorRegistry:
    """Register every connector. A new source is one more line here, nothing else."""
    registry = ConnectorRegistry()
    registry.register(OigLeieConnector(fetcher))
    registry.register(NyOmigConnector(fetcher))
    registry.register(NcMedicaidConnector(fetcher))
    registry.register(MdMedicaidConnector(fetcher))
    registry.register(VtMedicaidConnector(fetcher))
    registry.register(NdMedicaidConnector(fetcher))
    registry.register(WaHcaConnector(fetcher))
    registry.register(IaMedicaidConnector(fetcher))
    registry.register(InFssaConnector(fetcher))
    registry.register(PaMedicheckConnector(fetcher))
    return registry


def build_services(
    database: ConnectionSource, registry: ConnectorRegistry, clock: Clock
) -> Services:
    """Repositories over ``database`` and the use cases over them."""
    providers = PostgresProviderRepository(database)
    snapshots = PostgresSnapshotRepository(database)
    records = PostgresExclusionRecordRepository(database)
    verifications = PostgresVerificationRepository(database)
    return Services(
        registry=registry,
        clock=clock,
        providers=providers,
        snapshots=snapshots,
        records=records,
        verifications=verifications,
        sync_source=SyncSource(registry, snapshots, records, clock),
        source_status=SourceStatusReport(registry, snapshots, records, DEFAULT_FRESHNESS, clock),
        check_exclusions=CheckExclusions(
            registry, providers, records, snapshots, verifications, DEFAULT_FRESHNESS, clock
        ),
        lookup_exclusions=LookupExclusions(records, snapshots, registry),
        register_provider=RegisterProvider(providers, clock),
        list_providers=ListProviders(providers),
        verification_history=VerificationHistory(providers, verifications, records),
        provider_verdict=ProviderVerdictQuery(providers, verifications),
    )


def api_container(services: Services, settings: Settings) -> AppContainer:
    """What the API's route handlers see."""
    return AppContainer(
        register_provider=services.register_provider,
        list_providers=services.list_providers,
        source_status=services.source_status,
        check_exclusions=services.check_exclusions,
        lookup_exclusions=services.lookup_exclusions,
        verification_history=services.verification_history,
        provider_verdict=services.provider_verdict,
        providers=services.providers,
        organization_header_enabled=settings.environment == "local",
    )


@asynccontextmanager
async def runtime(settings: Settings) -> AsyncIterator[Services]:
    """Open the database pool and the HTTP client, yield the services, then close both."""
    clock = SystemClock()
    pool = create_pool(settings.database_url.get_secret_value())
    await pool.open()
    try:
        async with create_http_client(settings) as client:
            yield build_services(pool, build_registry(HttpFetcher(client, clock)), clock)
    finally:
        await pool.close()


def create_api(settings: Settings) -> FastAPI:
    """The HTTP application with its resources opened for the life of the process."""
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with runtime(settings) as services:
            app.state.container = api_container(services, settings)
            yield

    return create_app(expose_docs=settings.environment == "local", lifespan=lifespan)
