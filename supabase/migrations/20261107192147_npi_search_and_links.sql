-- P1 stage 2: the NPI mirror becomes searchable by name, and exclusion rows are linked to the
-- NPIs it holds. An inferred NPI never goes into exclusion_records.npi (the published column):
-- links live in exclusion_npi_links with their match level and evidence.
--
-- Cost: adding the stored key columns rewrites npi_registry once (6.9 GB, ten million rows,
-- under an exclusive lock), then builds three partial indexes: 192.5 s in all on the owner's
-- Mac (2026-11-07). The rewritten table has no visibility map until vacuumed, which makes
-- counts over it slow; run `vacuum (analyze) npi_registry` once afterwards (28 s locally;
-- VACUUM cannot run inside a migration). Loads keep working unchanged: the loader names its columns, and its work table
-- (`like npi_registry including defaults`) gets the key columns as plain columns it never
-- fills.


-- Name keys, computed in SQL on both sides of every join, so the mirror and the exclusion rows
-- can never disagree about what "the same name" is. name_key is the SQL counterpart of
-- credentialing.domain.names.normalize_name: accents folded (the Latin-1 and Latin Extended-A
-- letters whose decomposition is one ASCII letter, as Python's NFKD folds them), lower case,
-- apostrophes and periods dropped, every other run of non-alphanumerics one space, trimmed,
-- then trailing credential and generation tokens dropped while another token precedes them.
-- The token list is CREDENTIAL_SUFFIXES copied once; a test feeds the same names through both
-- and asserts equality. An empty result is null, so an empty name never matches.
create function name_key(name text) returns text
language sql
immutable
parallel safe
return nullif(
    regexp_replace(
        trim(
            regexp_replace(
                regexp_replace(
                    lower(
                        translate(
                            name,
                            'ÀÁÂÃÄÅÇÈÉÊËÌÍÎÏÑÒÓÔÕÖÙÚÛÜÝàáâãäåçèéêëìíîïñòóôõöùúûüýÿĀāĂăĄąĆćĈĉĊċČčĎďĒēĔĕĖėĘęĚěĜĝĞğĠġĢģĤĥĨĩĪīĬĭĮįİĴĵĶķĹĺĻļĽľŃńŅņŇňŌōŎŏŐőŔŕŖŗŘřŚśŜŝŞşŠšŢţŤťŨũŪūŬŭŮůŰűŲųŴŵŶŷŸŹźŻżŽžſ',
                            'aaaaaaceeeeiiiinooooouuuuyaaaaaaceeeeiiiinooooouuuuyyaaaaaaccccccccddeeeeeeeeeegggggggghhiiiiiiiiijjkkllllllnnnnnnoooooorrrrrrssssssssttttuuuuuuuuuuuuwwyyyzzzzzzs'
                        )
                    ),
                    '[''.’]', '', 'g'
                ),
                '[^0-9a-z]+', ' ', 'g'
            )
        ),
        '^(.*?\S)(?: (?:md|do|rn|lpn|pa|np|dds|dmd|pt|phd|jr|sr|ii|iii))+$', '\1'
    ),
    ''
);

comment on function name_key(text) is
    'A name as matching compares it; the SQL counterpart of domain.names.normalize_name.';


-- organization_key is normalize_organization_name: name_key, then a leading "the" and trailing
-- legal designators (LEGAL_DESIGNATORS copied once), repeatedly; a name made only of those
-- words keeps its name_key, never empty.
create function organization_key(name text) returns text
language sql
immutable
parallel safe
return coalesce(
    nullif(
        regexp_replace(
            regexp_replace(name_key(name), '^the(?: |$)', ''),
            '(?:^| )(?:inc|incorporated|llc|llp|lp|pllc|pc|ltd|limited|co|corp|corporation|company)'
            '(?: (?:inc|incorporated|llc|llp|lp|pllc|pc|ltd|limited|co|corp|corporation|company))*$',
            ''
        ),
        ''
    ),
    name_key(name)
);

comment on function organization_key(text) is
    'An organization name as matching compares it; domain.names.normalize_organization_name.';


-- Every other name of a mirror row under both keys, distinct and sorted. A generated column
-- cannot hold a subquery, so this immutable wrapper does (it only ever sees text).
create function npi_other_name_keys(names text[]) returns text[]
language sql
immutable
parallel safe
return array(
    select distinct key
    from unnest(names) as other (name),
        lateral (values (name_key(other.name)), (organization_key(other.name))) as keys (key)
    where key is not null
    order by key
);


-- One ALTER, one rewrite. The keys use the C collation: byte order, so an initial's range
-- (first_name_key >= 'j' and < 'k') is an index range, and comparisons are plain memcmp.
alter table npi_registry
    add column last_name_key text collate "C"
        generated always as (name_key(last_name)) stored,
    add column first_name_key text collate "C"
        generated always as (name_key(first_name)) stored,
    add column organization_key text collate "C"
        generated always as (organization_key(organization_name)) stored,
    add column other_name_keys text[]
        generated always as (npi_other_name_keys(other_names)) stored;

comment on column npi_registry.last_name_key is
    'name_key(last_name), the candidate search key with first_name_key (generated).';
comment on column npi_registry.organization_key is
    'organization_key(organization_name), the organization search key (generated).';
comment on column npi_registry.other_name_keys is
    'Every other name under name_key and organization_key, distinct (generated).';

set maintenance_work_mem = '512MB';

-- Partial on in_file: every reader asks only for NPIs the latest full file lists. Rows
-- without the key (organizations, deactivated stubs) are left out of the name indexes; an
-- equality on the key implies it is not null, so the planner still uses them.
create index npi_registry_person_key_idx
    on npi_registry (last_name_key, first_name_key)
    where in_file and last_name_key is not null;
create index npi_registry_organization_key_idx
    on npi_registry (organization_key)
    where in_file and organization_key is not null;
create index npi_registry_other_name_keys_idx
    on npi_registry using gin (other_name_keys)
    where in_file;

reset maintenance_work_mem;

analyze npi_registry;

comment on table npi_registry is
    'The local mirror of the NPPES NPI registry (shared reference data): one row per NPI, its '
    'newest version; replaced versions are in npi_registry_history. Rows are never deleted: '
    'in_file turns false when a full file no longer lists the NPI. Searched by the generated '
    'name keys (partial indexes on in_file).';


-- Links from exclusion rows to mirror NPIs: one row per (record, candidate) with its match
-- level, or one row with npi null and level 'none' when nothing was found, so "not linked" is
-- always distinguishable from "never checked" (no row). A record's links are rewritten whole
-- (delete, insert) by each linking run; nothing is updated in place. The unique index also
-- serves lookups by record_id, so no separate index on it.
create table exclusion_npi_links (
    id uuid primary key,
    record_id uuid not null references exclusion_records,
    npi char(10) references npi_registry,
    level text not null,
    evidence jsonb not null default '[]',
    explanation text not null,
    candidates integer not null check (candidates >= 0),
    mirror_snapshot_id uuid references source_snapshots,
    linked_at timestamptz not null,
    check ((npi is null) = (level = 'none')),
    unique nulls not distinct (record_id, npi)
);

create index exclusion_npi_links_npi_idx on exclusion_npi_links (npi) where npi is not null;

comment on table exclusion_npi_links is
    'Inferred links from exclusion rows to NPPES NPIs (shared reference data), never written '
    'into exclusion_records.npi. candidates is how many mirror rows the discovery found for '
    'the record (51 means more than 50); mirror_snapshot_id the NPPES load they were made '
    'against, so a newer load makes them stale.';


-- One row per linking run: what triggered it, how many records it linked by best level, and
-- its phase timings (json, not jsonb, so the phases keep their order) and CPU time.
create table npi_link_runs (
    id uuid primary key,
    started_at timestamptz not null,
    finished_at timestamptz not null,
    trigger text not null,
    mirror_snapshot_id uuid references source_snapshots,
    records integer not null check (records >= 0),
    linked integer not null check (linked >= 0),
    by_level jsonb not null,
    cpu_time interval,
    phases json,
    note text
);

create index npi_link_runs_finished_idx on npi_link_runs (finished_at desc);

comment on table npi_link_runs is
    'Linking runs (shared reference data): trigger (list_sync:<source_id>, nppes_load, cli), '
    'records linked, records with at least one link, records by best level, timings.';
