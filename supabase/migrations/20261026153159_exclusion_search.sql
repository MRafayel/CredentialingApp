-- Direct lookup of the exclusion lists: a full-text document per row over every name and
-- identifier, and an index for candidate search by licence number.
--
-- concat_ws and array_to_string are only STABLE in Postgres (they accept any type), so a
-- generated column cannot call them directly. This wrapper only ever sees text, for which
-- both are deterministic, so declaring it IMMUTABLE is truthful. The 'simple' configuration
-- lower-cases and splits words without stemming or stop words, so names and identifiers stay
-- searchable exactly as published. Names carry weight A and identifiers weight D, so a word
-- found in a name ranks above the same prefix found inside an NPI or licence number.

create function exclusion_search_document(
    last_name text,
    first_name text,
    middle_name text,
    organization_name text,
    aliases text[],
    npi text,
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
    to_tsvector(
        'simple',
        concat_ws(
            ' ',
            coalesce(npi, ''),
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
            npi,
            state_provider_id,
            license_number
        )
    ) stored;

create index exclusion_records_current_search_idx
    on exclusion_records using gin (search_vector) where valid_to is null;

-- Licence numbers compared the way matching compares them: no whitespace, upper case.
create index exclusion_records_current_license_idx
    on exclusion_records (upper(regexp_replace(license_number, '\s+', '', 'g')))
    where valid_to is null;
