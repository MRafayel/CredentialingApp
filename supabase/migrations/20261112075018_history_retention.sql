-- History retention: verifications stay append-only, with one permitted delete, the purge of
-- rows older than the retention window the application holds (six months). The purge sets the
-- transaction-local setting credentialing.history_purge to 'on' right before its delete, in
-- the same transaction; any other delete, every update and a truncate are still rejected.


create or replace function verifications_reject_change() returns trigger
language plpgsql as $$
begin
    -- The purge is the one permitted delete: the application deletes only results older than
    -- its retention policy, and never a provider's newest result of a source or newest NPI
    -- result, so a verdict and the comparison a change is detected by are kept.
    if tg_op = 'DELETE' and current_setting('credentialing.history_purge', true) = 'on' then
        return old;
    end if;
    raise exception 'verifications is append-only: % is not allowed', tg_op;
end;
$$;

comment on function verifications_reject_change() is
    'Keeps verifications append-only: rejects every update and truncate, and every delete '
    'except the history purge, which sets credentialing.history_purge to on for its own '
    'transaction under the retention policy the application holds.';

comment on table verifications is
    'Verification history, append-only: a new verification is a new row and earlier rows are '
    'never updated. Changes are detected by comparing a row with the previous one. The one '
    'delete is the retention purge of rows older than six months, which keeps each '
    'provider''s newest result per source and newest NPI result.';
