-- Licence numbers are compared without punctuation, prefixes' separators or spaces: the lists
-- and NPPES write the same licence differently ("036123456" against "036-123456" or
-- "036.123456"; New York's formats matched in 3 of 1,116 cases under the old whitespace-only
-- key). license_key is the SQL counterpart of domain.policies.license_key and is used on both
-- sides of every licence comparison (candidate search, same-licence records, NPI linking), so
-- the database and the matching never disagree about "the same licence".

create function license_key(number text) returns text
language sql
immutable
parallel safe
return nullif(upper(regexp_replace(number, '[^A-Za-z0-9]', '', 'g')), '');

comment on function license_key(text) is
    'A licence number as matching compares it: letters and digits only, upper case; the SQL '
    'counterpart of domain.policies.license_key.';

drop index exclusion_records_latest_license_idx;
create index exclusion_records_latest_license_idx
    on exclusion_records (license_key(license_number))
    where latest;
