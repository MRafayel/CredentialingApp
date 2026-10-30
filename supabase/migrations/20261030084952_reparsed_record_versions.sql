-- A row can only be removed from a published list when the list itself changed. Rows closed
-- by a sync whose snapshot has the same bytes (content_hash) as the source's previous
-- completed snapshot were superseded by a re-parse (a parser change under --force changed
-- their identity hash), not removed: they stop being latest, so they never read as
-- "removed from published list".
--
-- The closing snapshot of a row is the snapshot of the rows the same sync inserted (their
-- valid_from equals the closed row's valid_to, since one sync uses one "now"); when that sync
-- inserted nothing, the newest completed snapshot of the source fetched at or before the
-- closing (a sync stores its snapshot before it replaces the records). The previous snapshot
-- is the newest other completed snapshot of the source with a hash, fetched no later.

with removed as (
    select
        r.id,
        r.source_id,
        coalesce(
            (
                select n.snapshot_id
                from exclusion_records as n
                where n.source_id = r.source_id and n.valid_from = r.valid_to
                order by n.id
                limit 1
            ),
            (
                select s.id
                from source_snapshots as s
                where s.source_id = r.source_id
                  and s.outcome = 'completed'
                  and s.fetched_at <= r.valid_to
                order by s.fetched_at desc, s.id desc
                limit 1
            )
        ) as closing_id
    from exclusion_records as r
    where r.latest and r.valid_to is not null
),
reparsed as (
    select removed.id
    from removed
    join source_snapshots as closing on closing.id = removed.closing_id
    where closing.content_hash is not null
      and closing.content_hash = (
          select p.content_hash
          from source_snapshots as p
          where p.source_id = removed.source_id
            and p.outcome = 'completed'
            and p.content_hash is not null
            and p.id <> closing.id
            and p.fetched_at <= closing.fetched_at
          order by p.fetched_at desc, p.id desc
          limit 1
      )
)
update exclusion_records
set latest = false
where id in (select id from reparsed);
