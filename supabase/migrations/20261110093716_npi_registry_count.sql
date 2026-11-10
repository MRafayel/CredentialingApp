-- The NPI mirror's row counts, stored and kept exact by Postgres, so the sources report reads
-- one row instead of counting ten million (an index-only scan of the 298 MB primary key: 4.3 s
-- with the cache half warm, about 10 s cold, on every request; 2026-11-10).
--
-- Statement-level triggers with transition tables add the difference each statement makes, so
-- every writer is covered without naming it: the loader's COPY straight into the mirror, its
-- inserts, upsert (an INSERT ... ON CONFLICT DO UPDATE fires the insert and the update
-- triggers, each with its own rows), the deactivation and in_file updates, and live answers
-- (which go through the same loader). Creating the triggers locks npi_registry against writes
-- until this migration's transaction ends, so the count taken after them misses nothing.


create table npi_registry_count (
    singleton boolean primary key default true check (singleton),
    in_file bigint not null,
    active bigint not null
);

comment on table npi_registry_count is
    'The NPI mirror''s row counts (one row), maintained by the npi_registry_count triggers on '
    'npi_registry; never written by application code.';

comment on column npi_registry_count.singleton is
    'Always true: the primary key that allows a single row.';

comment on column npi_registry_count.in_file is
    'npi_registry rows with in_file: the NPIs the latest file lists.';

comment on column npi_registry_count.active is
    'npi_registry rows with in_file and status ''active''.';


-- One function for the three events; each trigger declares only the transition tables its
-- event has (a trigger with transition tables fires on one event), and the function reads only
-- those of TG_OP. A statement that changes neither count writes nothing.
create function npi_registry_count_apply() returns trigger
language plpgsql
as $$
declare
    added_in_file bigint := 0;
    added_active bigint := 0;
    removed_in_file bigint := 0;
    removed_active bigint := 0;
begin
    if tg_op in ('INSERT', 'UPDATE') then
        select count(*) filter (where in_file),
               count(*) filter (where in_file and status = 'active')
        into added_in_file, added_active
        from new_rows;
    end if;
    if tg_op in ('UPDATE', 'DELETE') then
        select count(*) filter (where in_file),
               count(*) filter (where in_file and status = 'active')
        into removed_in_file, removed_active
        from old_rows;
    end if;
    if added_in_file <> removed_in_file or added_active <> removed_active then
        update npi_registry_count
        set in_file = in_file + added_in_file - removed_in_file,
            active = active + added_active - removed_active;
    end if;
    return null;
end;
$$;

comment on function npi_registry_count_apply() is
    'Adds what one statement on npi_registry changed to npi_registry_count.';

create function npi_registry_count_reset() returns trigger
language plpgsql
as $$
begin
    update npi_registry_count set in_file = 0, active = 0;
    return null;
end;
$$;

comment on function npi_registry_count_reset() is
    'Sets npi_registry_count to zero when npi_registry is truncated (tests truncate the mirror '
    'inside their transaction).';

create trigger npi_registry_count_insert
after insert on npi_registry
referencing new table as new_rows
for each statement execute function npi_registry_count_apply();

create trigger npi_registry_count_update
after update on npi_registry
referencing old table as old_rows new table as new_rows
for each statement execute function npi_registry_count_apply();

create trigger npi_registry_count_delete
after delete on npi_registry
referencing old table as old_rows
for each statement execute function npi_registry_count_apply();

create trigger npi_registry_count_truncate
after truncate on npi_registry
for each statement execute function npi_registry_count_reset();


insert into npi_registry_count (singleton, in_file, active)
select true,
       count(*) filter (where in_file),
       count(*) filter (where in_file and status = 'active')
from npi_registry;
