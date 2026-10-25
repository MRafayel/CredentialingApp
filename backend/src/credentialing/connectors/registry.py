"""The connector registry: the orchestrator finds connectors here by capability, never by name.

Adding a source means registering its connector; nothing branches on a state or a source.
"""

from credentialing.domain.enums import CredentialType
from credentialing.domain.ports import BulkListConnector, LookupConnector
from credentialing.domain.types import FEDERAL, SourceId, StateCode

type Connector = BulkListConnector | LookupConnector


class DuplicateSourceError(ValueError):
    """Two connectors were registered for the same source id."""


class ConnectorRegistry:
    """Connectors by source id, selectable by credential type and jurisdiction."""

    def __init__(self) -> None:
        self._connectors: dict[SourceId, Connector] = {}

    def register(self, connector: Connector) -> None:
        """Add a connector; each source id may be registered once."""
        source_id = connector.capabilities.source_id
        if source_id in self._connectors:
            raise DuplicateSourceError(f"a connector for {source_id!r} is already registered")
        self._connectors[source_id] = connector

    def get(self, source_id: SourceId) -> Connector | None:
        """Return the connector for ``source_id``, if one is registered."""
        return self._connectors.get(source_id)

    def bulk_connectors_for(
        self, credential_type: CredentialType, jurisdiction: StateCode
    ) -> list[BulkListConnector]:
        """Bulk-list connectors covering this credential type in this jurisdiction."""
        return [
            connector
            for connector in self._covering(credential_type, jurisdiction)
            if isinstance(connector, BulkListConnector)
        ]

    def lookup_connectors_for(
        self, credential_type: CredentialType, jurisdiction: StateCode
    ) -> list[LookupConnector]:
        """Per-provider lookup connectors covering this credential type in this jurisdiction."""
        return [
            connector
            for connector in self._covering(credential_type, jurisdiction)
            if isinstance(connector, LookupConnector)
        ]

    def _covering(
        self, credential_type: CredentialType, jurisdiction: StateCode
    ) -> list[Connector]:
        """Connectors for the credential type that cover this jurisdiction or are federal."""
        return [
            connector
            for connector in self._connectors.values()
            if connector.capabilities.credential_type is credential_type
            and not connector.capabilities.jurisdictions.isdisjoint({jurisdiction, FEDERAL})
        ]
