-- latest marks the newest version of a row identity (source_id, record_hash), whether it is
-- current (valid_to is null) or was removed from the published list (valid_to is set). Reads
-- match against latest rows, so a provider who left a list keeps matching with the "removed"
-- status instead of silently disappearing: a list dropping a row does not prove reinstatement.
-- A changed row's old version is closed and loses latest; the new version takes it.

alter table exclusion_records add column latest boolean not null default true;

update exclusion_records as older
set latest = false
where exists (
    select 1
    from exclusion_records as newer
    where newer.source_id = older.source_id
      and newer.record_hash = older.record_hash
      and newer.valid_from > older.valid_from
);

comment on column exclusion_records.latest is
    'True on the newest version of a row identity, current or removed from the published list. '
    'Reads match against latest rows; valid_to says whether that version is still listed.';

-- The read-path indexes cover latest rows instead of current ones. The unique
-- one-current-row-per-identity index and the versions index stay as they are.
drop index exclusion_records_current_npi_idx;
drop index exclusion_records_current_name_keys_idx;
drop index exclusion_records_current_search_idx;
drop index exclusion_records_current_license_idx;
drop index exclusion_records_current_related_npis_idx;

create index exclusion_records_latest_npi_idx
    on exclusion_records (npi) where latest;
create index exclusion_records_latest_name_keys_idx
    on exclusion_records using gin (name_keys) where latest;
create index exclusion_records_latest_search_idx
    on exclusion_records using gin (search_vector) where latest;
create index exclusion_records_latest_license_idx
    on exclusion_records (upper(regexp_replace(license_number, '\s+', '', 'g')))
    where latest;
create index exclusion_records_latest_related_npis_idx
    on exclusion_records using gin (related_npis) where latest;

-- At most one latest version per row identity.
create unique index exclusion_records_latest_hash_idx
    on exclusion_records (source_id, record_hash) where latest;
