-- The NPI registry mirror: the NPPES Data Dissemination files loaded by difference, and the
-- load statistics of each fetch. Shared reference data: no organization owns these rows.

-- json, not jsonb: the phases are kept in the order they ran, which jsonb would sort away.
-- The column is only ever read back whole, never queried into.
alter table source_snapshots add column load json;

comment on column source_snapshots.load is
    'Counts, phase timings and CPU time of the dataset load this fetch produced (DatasetLoad); '
    'null when the fetch loaded nothing.';


create table npi_registry (
    npi char(10) primary key check (npi ~ '^[0-9]{10}$'),
    entity_kind text not null,
    status text not null,
    replacement_npi char(10),
    last_name text,
    first_name text,
    middle_name text,
    name_prefix text,
    suffix text,
    credential text,
    organization_name text,
    other_names text[] not null default '{}',
    sole_proprietor boolean,
    organization_subpart boolean,
    enumeration_date date,
    last_updated date,
    certification_date date,
    deactivation_date date,
    deactivation_reason text,
    reactivation_date date,
    mailing_address jsonb,
    practice_address jsonb,
    taxonomies jsonb not null default '[]',
    authorized_official jsonb,
    line_hash text not null,
    in_file boolean not null default true,
    snapshot_id uuid not null references source_snapshots,
    first_seen_at timestamptz not null,
    updated_at timestamptz not null
);

comment on table npi_registry is
    'The local mirror of the NPPES NPI registry (shared reference data): one row per NPI, its '
    'newest version; replaced versions are in npi_registry_history. Rows are never deleted: '
    'in_file turns false when a full file no longer lists the NPI. No index beyond the primary '
    'key yet: search indexes come with the first reader of the mirror, and every extra index '
    'would slow the load of ten million rows.';

comment on column npi_registry.line_hash is
    'SHA-256 of the file row last loaded (or of "deactivated|MM/DD/YYYY" for a row only the '
    'deactivation file wrote); a load compares it to skip unchanged rows without parsing them.';


create table npi_registry_history (
    npi char(10) not null,
    entity_kind text not null,
    status text not null,
    replacement_npi char(10),
    last_name text,
    first_name text,
    middle_name text,
    name_prefix text,
    suffix text,
    credential text,
    organization_name text,
    other_names text[] not null default '{}',
    sole_proprietor boolean,
    organization_subpart boolean,
    enumeration_date date,
    last_updated date,
    certification_date date,
    deactivation_date date,
    deactivation_reason text,
    reactivation_date date,
    mailing_address jsonb,
    practice_address jsonb,
    taxonomies jsonb not null default '[]',
    authorized_official jsonb,
    line_hash text not null,
    in_file boolean not null,
    snapshot_id uuid not null,
    first_seen_at timestamptz not null,
    updated_at timestamptz not null,
    valid_from timestamptz not null,
    valid_to timestamptz not null,
    primary key (npi, valid_to)
);

comment on table npi_registry_history is
    'Versions of npi_registry rows that a later file replaced (insert-only): valid_from is the '
    'replaced row''s updated_at, valid_to the moment of the load that replaced it.';
