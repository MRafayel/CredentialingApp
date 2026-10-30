-- A list sometimes rewrites a field that is part of a row's identity (a spelling, a licence
-- number). The old identity then vanishes and a new one appears in the same sync. When the
-- source changed and exactly one vanished row and exactly one new row share an NPI and an
-- effective date, the new row records the one it replaced in rewrites, and the old row is
-- closed and loses latest: it was rewritten, not removed from the published list.

alter table exclusion_records
    add column rewrites uuid null references exclusion_records (id);

comment on column exclusion_records.rewrites is
    'The row this version replaced when the list rewrote an identity field; same NPI and '
    'effective date, one to one within a sync.';

create index exclusion_records_rewrites_idx
    on exclusion_records (rewrites) where rewrites is not null;

-- Repair: rows closed as removed (latest with valid_to set) that pair one to one with a row
-- of the same source first seen at that moment (valid_from equals the closing valid_to, since
-- one sync uses one "now"), with the same NPI and effective date, were rewritten. A new row
-- counts only as the first version of its identity, as a sync pairs only never-seen rows.

with vanished as (
    select id, source_id, npi, effective_date, valid_to as synced_at
    from exclusion_records
    where latest and valid_to is not null and npi is not null
),
arrived as (
    select n.id, n.source_id, n.npi, n.effective_date, n.valid_from as synced_at
    from exclusion_records as n
    where n.npi is not null
      and n.rewrites is null
      and exists (
          select 1
          from vanished as v
          where v.source_id = n.source_id and v.synced_at = n.valid_from
      )
      and not exists (
          select 1
          from exclusion_records as e
          where e.source_id = n.source_id
            and e.record_hash = n.record_hash
            and e.valid_from < n.valid_from
      )
),
old_rows as (
    select source_id, npi, effective_date, synced_at, (array_agg(id))[1] as id
    from vanished
    group by source_id, npi, effective_date, synced_at
    having count(*) = 1
),
new_rows as (
    select source_id, npi, effective_date, synced_at, (array_agg(id))[1] as id
    from arrived
    group by source_id, npi, effective_date, synced_at
    having count(*) = 1
),
pairs as (
    select o.id as old_id, a.id as new_id
    from old_rows as o
    join new_rows as a
      on a.source_id = o.source_id
     and a.npi = o.npi
     and a.effective_date is not distinct from o.effective_date
     and a.synced_at = o.synced_at
),
superseded as (
    update exclusion_records
    set latest = false
    where id in (select old_id from pairs)
    returning id
)
update exclusion_records as n
set rewrites = pairs.old_id
from pairs
where n.id = pairs.new_id;
