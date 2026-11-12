-- One NPI per roster: an organization's providers never share an NPI.


-- Rows breaking the rule can exist only in local development databases (two pairs left by
-- manual tests on 2026-11-11); nothing is deployed yet, so they are removed rather than kept.
-- Of each (organization_id, npi) the earliest row stays (created_at, id: the row
-- find_by_npi returns) and the later rows go, with their verifications (that foreign key has
-- no cascade) and their stored status (which cascades). Verifications are append-only, so
-- their guard is lifted for this one statement, inside this migration's transaction. On a
-- database without such rows nothing is deleted.
alter table verifications disable trigger verifications_append_only;

with duplicates as (
    select id, organization_id
    from (
        select id, organization_id,
            row_number() over (partition by organization_id, npi order by created_at, id) as rank
        from providers
        where npi is not null
    ) ranked
    where rank > 1
),
removed_verifications as (
    delete from verifications v
    using duplicates d
    where v.organization_id = d.organization_id and v.provider_id = d.id
)
delete from providers p
using duplicates d
where p.organization_id = d.organization_id and p.id = d.id;

alter table verifications enable trigger verifications_append_only;


-- The unique index serves the lookups by NPI the plain index served.
create unique index providers_organization_npi_key
    on providers (organization_id, npi)
    where npi is not null;

drop index providers_organization_npi_idx;

comment on index providers_organization_npi_key is
    'One NPI per roster: an organization holds each NPI on one provider at most. The '
    'application maps a violation to a 409 that names the provider holding the NPI when known.';
