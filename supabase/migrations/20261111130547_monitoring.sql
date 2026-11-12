-- Provider monitoring: roster roles, "opened at", what changed on each result, the derived
-- provider status the roster is ordered by, and the recheck runs.


alter table providers
    add column role text not null default 'practitioner'
        check (role in ('practitioner', 'workforce', 'vendor')),
    add column last_viewed_at timestamptz;

comment on column providers.role is
    'Why the party is on the roster: practitioner (an NPI is expected), workforce (employees, '
    'volunteers, managers) or vendor (contractors, suppliers, affiliated organizations).';

comment on column providers.last_viewed_at is
    'When someone last opened the provider''s page; results changed before it no longer read '
    'as changed.';

-- The roster is listed newest first within an organization.
create index providers_organization_created_idx on providers (organization_id, created_at desc, id desc);


alter table verifications add column changed_from jsonb;

comment on column verifications.changed_from is
    'The previous result''s answer (outcome, finding, record_status, registry_status, '
    'verified_at) when this result differs from it; null on a first result and when nothing '
    'changed. Written once with the row, like every column of this append-only table.';

-- The newest changed result per provider, for the provider status.
create index verifications_changed_idx
    on verifications (organization_id, provider_id, created_at desc)
    where changed_from is not null;


-- Derived state, updated normally: the status the domain derived for each provider from its
-- latest results, stored so a roster page can be ordered and counted by attention without
-- reading every provider's history. The rules live in the application (domain policies);
-- this table only keeps their output and the version of the rules that produced it, so rows
-- of an older version are derived again before they are read.
create table provider_status (
    provider_id uuid primary key,
    organization_id uuid not null,
    verdict text not null,
    identity text not null,
    changed boolean not null,
    changed_at timestamptz,
    attention text,
    attention_rank smallint not null check (attention_rank >= 0),
    last_checked_at timestamptz,
    next_check_at timestamptz,
    listed_in text[] not null default '{}',
    flagged_in text[] not null default '{}',
    review_in text[] not null default '{}',
    unchecked text[] not null default '{}',
    rules_version integer not null,
    updated_at timestamptz not null,
    -- A status can only belong to a provider of the same organization.
    foreign key (organization_id, provider_id) references providers (organization_id, id)
        on delete cascade
);

create index provider_status_attention_idx on provider_status (organization_id, attention_rank);

comment on table provider_status is
    'Each provider''s monitoring status as the domain derived it (derived state, updated '
    'normally). Tenant data: every query filters by organization_id.';

comment on column provider_status.attention_rank is
    'The attention reason''s priority as the domain ranks it (0 most serious); the count of '
    'reasons when nothing needs a person. Ordering only.';

comment on column provider_status.changed_at is
    'When the newest result that differs from the one before it was stored (null: none); '
    'changed says whether someone opened the provider since.';

comment on column provider_status.rules_version is
    'The version of the status rules that derived this row; another version is derived again.';

alter table provider_status enable row level security;


-- One row per recheck run, modelled on npi_link_runs.
create table monitoring_runs (
    id uuid primary key,
    started_at timestamptz not null,
    finished_at timestamptz not null,
    trigger text not null,
    source_id text,
    organizations integer not null check (organizations >= 0),
    providers integer not null check (providers >= 0),
    results integer not null check (results >= 0),
    changed integer not null check (changed >= 0),
    failures integer not null check (failures >= 0),
    purged integer not null default 0 check (purged >= 0),
    phases json,
    cpu_time interval,
    note text
);

create index monitoring_runs_finished_idx on monitoring_runs (finished_at desc);

comment on table monitoring_runs is
    'Recheck runs of the system job (shared system table, not tenant data: counts and timings '
    'only, no provider or organization ids): trigger (due, list_sync:<source_id>, nppes_load, '
    'cli, import and add for the first check of providers just added, or npi_set for the '
    'lists after a user confirmed an NPI), the list rechecked (null: every list), counts, '
    'phase timings (json, so they keep their order) and CPU time. purged counts the history '
    'rows older than the retention window that a due or cli run deleted.';
