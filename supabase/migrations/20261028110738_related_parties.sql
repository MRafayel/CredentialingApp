-- Other parties named on an exclusion row. Some lists name several parties in one row (a
-- business with its owner, a person with the business they practise as) and publish several
-- NPIs in one cell without saying whose each is. related_npis holds the valid NPIs besides the
-- row's own npi, related_parties the names of the other parties; aliases stay other names of
-- the same party.
--
-- The search document gains both: related parties at weight B (below the party's own names
-- at A), related NPIs with the other identifiers at weight D. A generated column cannot be
-- altered in place, so the column and its index are dropped and re-created over the new
-- function; the function is IMMUTABLE for the same reason as before (it only sees text).

alter table exclusion_records
    add column related_npis text[] not null default '{}',
    add column related_parties text[] not null default '{}';

create index exclusion_records_current_related_npis_idx
    on exclusion_records using gin (related_npis) where valid_to is null;

alter table exclusion_records drop column search_vector;

drop function exclusion_search_document(text, text, text, text, text[], text, text, text);

create function exclusion_search_document(
    last_name text,
    first_name text,
    middle_name text,
    organization_name text,
    aliases text[],
    related_parties text[],
    npi text,
    related_npis text[],
    state_provider_id text,
    license_number text
) returns tsvector
language sql
immutable
parallel safe
return setweight(
    to_tsvector(
        'simple',
        concat_ws(
            ' ',
            coalesce(last_name, ''),
            coalesce(first_name, ''),
            coalesce(middle_name, ''),
            coalesce(organization_name, ''),
            array_to_string(aliases, ' ')
        )
    ),
    'A'
) || setweight(
    to_tsvector('simple', array_to_string(related_parties, ' ')),
    'B'
) || setweight(
    to_tsvector(
        'simple',
        concat_ws(
            ' ',
            coalesce(npi, ''),
            array_to_string(related_npis, ' '),
            coalesce(state_provider_id, ''),
            coalesce(license_number, '')
        )
    ),
    'D'
);

alter table exclusion_records
    add column search_vector tsvector generated always as (
        exclusion_search_document(
            last_name,
            first_name,
            middle_name,
            organization_name,
            aliases,
            related_parties,
            npi,
            related_npis,
            state_provider_id,
            license_number
        )
    ) stored;

create index exclusion_records_current_search_idx
    on exclusion_records using gin (search_vector) where valid_to is null;

-- A record's history is every version of one row identity in its list, current or not. The
-- existing hash index covers only current rows, so the versions query needs its own.
create index exclusion_records_versions_idx
    on exclusion_records (source_id, record_hash);

-- Which role on the row a verification matched the provider as: 'party' (the listed party) or
-- 'related_party' (another party the row names). Null on earlier rows, which were the party.
alter table verifications add column match_relation text;
