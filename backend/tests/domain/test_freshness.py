from datetime import timedelta

import pytest
from pydantic import ValidationError

from credentialing.domain.enums import CredentialType, Freshness, VerificationMethod
from credentialing.domain.policies import DEFAULT_FRESHNESS, FreshnessPolicy, FreshnessPolicyTable
from tests.synthetic import NOW

POLICY = FreshnessPolicy(max_age=timedelta(days=7))
TICK = timedelta(microseconds=1)


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (timedelta(0), Freshness.LIVE),
        (timedelta(minutes=1), Freshness.LIVE),
        (timedelta(minutes=1) + TICK, Freshness.CACHED),
        (timedelta(days=7), Freshness.CACHED),
        (timedelta(days=7) + TICK, Freshness.STALE),
    ],
)
def test_freshness_boundaries(age: timedelta, expected: Freshness) -> None:
    assert POLICY.evaluate(NOW - age, NOW, VerificationMethod.HTTP) is expected


def test_manual_verification_is_always_manual() -> None:
    assert POLICY.evaluate(NOW, NOW, VerificationMethod.MANUAL) is Freshness.MANUAL
    old = NOW - timedelta(days=365)
    assert POLICY.evaluate(old, NOW, VerificationMethod.MANUAL) is Freshness.MANUAL


def test_defaults_cover_every_credential_type() -> None:
    windows = {
        kind: DEFAULT_FRESHNESS.policy_for(kind, "any_source").max_age for kind in CredentialType
    }

    assert windows == {
        CredentialType.EXCLUSION: timedelta(days=7),
        CredentialType.NPI: timedelta(days=30),
        CredentialType.STATE_LICENSE: timedelta(days=1),
        CredentialType.BOARD_CERTIFICATION: timedelta(days=30),
    }


def test_a_source_policy_overrides_the_default() -> None:
    daily = FreshnessPolicy(max_age=timedelta(days=1))
    table = FreshnessPolicyTable(
        policies={**DEFAULT_FRESHNESS.policies, (CredentialType.EXCLUSION, "ny_omig"): daily}
    )

    assert table.policy_for(CredentialType.EXCLUSION, "ny_omig") == daily
    assert table.policy_for(CredentialType.EXCLUSION, "oig_leie").max_age == timedelta(days=7)


def test_a_table_without_a_default_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no default freshness policy"):
        FreshnessPolicyTable(policies={(CredentialType.EXCLUSION, None): POLICY})
