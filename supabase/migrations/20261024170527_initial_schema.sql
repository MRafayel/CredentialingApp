-- Initial schema: tenants, their providers, public source snapshots and exclusion records,
-- and the append-only verification history.
--
-- Row level security is enabled on every table an organization owns, with no policies yet:
-- roles other than the table owner see nothing until policies are written. The backend
-- connects as the owner and scopes every query by organization_id itself.

create table organizations (
    id uuid primary key,
    name text not null,
    created_at timestamptz not null default now()
);

comment on table organizations is
    'Customers. An individual customer is an organization of one.';

alter table organizations enable row level security;


create table providers (
    id uuid primary key,
    organization_id uuid not null references organizations,
    entity_kind text not null,
    last_name text,
    first_name text,
    middle_name text,
    suffix text,
    organization_name text,
    npi char(10) check (npi ~ '^[0-9]{10}$'),
    date_of_birth date,
    licenses jsonb not null default '[]',
    addresses jsonb not null default '[]',
    taxonomy_codes text[] not null default '{}',
    created_at timestamptz not null,
    -- Lets verifications reference a provider together with its organization, so a result
    -- can never point at another tenant's provider.
    unique (organization_id, id)
);

create index providers_organization_npi_idx on providers (organization_id, npi);

comment on table providers is
    'An organization''s provider roster. Every query filters by organization_id.';

alter table providers enable row level security;


create table source_snapshots (
    id uuid primary key,
    source_id text not null,
    fetched_at timestamptz not null,
    outcome text not null,
    source_url text not null,
    content_hash text,
    etag text,
    last_modified timestamptz,
    byte_size bigint,
    row_count integer,
    note text
);

create index source_snapshots_source_fetched_idx on source_snapshots (source_id, fetched_at desc);

comment on table source_snapshots is
    'One row per fetch of a public source, whatever its outcome. Shared reference data.';


create table exclusion_records (
    id uuid primary key,
    source_id text not null,
    snapshot_id uuid not null references source_snapshots,
    -- record_hash is the row's identity across snapshots; content_hash covers what can change
    -- under that identity (end date, reason, raw cells...). A changed content_hash closes the
    -- old version and inserts a new one.
    record_hash text not null,
    content_hash text not null,
    entity_kind text not null,
    last_name text,
    first_name text,
    middle_name text,
    suffix text,
    organization_name text,
    aliases text[] not null default '{}',
    -- Normalized last name, organization name and alias words, computed by the domain's name
    -- normalization, so candidate search compares names the same way matching does.
    name_keys text[] not null default '{}',
    npi char(10),
    state_provider_id text,
    license_number text,
    license_state text,
    license_kind text,
    date_of_birth date,
    address jsonb,
    action text not null,
    action_raw text,
    effective_date date,
    end_date date,
    reason text,
    raw jsonb not null,
    valid_from timestamptz not null,
    valid_to timestamptz
);

create unique index exclusion_records_current_hash_idx
    on exclusion_records (source_id, record_hash) where valid_to is null;
create index exclusion_records_current_npi_idx
    on exclusion_records (npi) where valid_to is null;
create index exclusion_records_current_name_keys_idx
    on exclusion_records using gin (name_keys) where valid_to is null;

comment on table exclusion_records is
    'Normalized exclusion list rows, shared reference data. A row is current while valid_to is '
    'null; a row that leaves the list or changes is closed with valid_to, never deleted, so '
    'removals (often the only sign of reinstatement) and earlier versions stay visible.';


create table verifications (
    id uuid primary key,
    organization_id uuid not null references organizations,
    provider_id uuid not null,
    credential_type text not null,
    source_id text not null,
    outcome text not null,
    finding text,
    match_level text,
    match_evidence jsonb,
    match_explanation text,
    matched_record_ids uuid[] not null default '{}',
    summary text not null,
    details jsonb not null default '{}',
    source_url text not null,
    method text not null,
    verified_at timestamptz not null,
    source_updated_at timestamptz,
    snapshot_id uuid references source_snapshots,
    raw_record_ref text,
    freshness text not null,
    created_at timestamptz not null,
    foreign key (organization_id, provider_id) references providers (organization_id, id),
    -- A check that could not run has no finding, so it can never read as a clean result.
    check ((outcome = 'completed') = (finding is not null))
);

create index verifications_latest_idx
    on verifications (organization_id, provider_id, credential_type, source_id, created_at desc);

comment on table verifications is
    'Verification history, append-only: a new verification is a new row and earlier rows are '
    'never updated or deleted. Changes are detected by comparing a row with the previous one.';

alter table verifications enable row level security;

create function verifications_reject_change() returns trigger
language plpgsql as $$
begin
    raise exception 'verifications is append-only: % is not allowed', tg_op;
end;
$$;

create trigger verifications_append_only
    before update or delete on verifications
    for each row execute function verifications_reject_change();

create trigger verifications_no_truncate
    before truncate on verifications
    for each statement execute function verifications_reject_change();
