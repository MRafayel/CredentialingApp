-- Licence numbers are also found by their digits (owner, 2026-11-12): the lists and NPPES pad
-- one licence differently (New York "00000551" against "000551") and write a profession's
-- letters or code on one side only ("PSY10213" against "10213", "036-123456" against
-- "123456"), so under license_key alone 4 of 1,326 New York pairs of the same person agreed.
-- license_digits is the SQL counterpart of domain.policies.license_digits: the longest run of
-- digits (the first of them when several are as long) without its leading zeros, null when
-- the number has none. It finds the candidates (candidate search, same-licence records, the
-- NPI discovery's ranking); whether the digits alone are enough (the floor of four digits,
-- parts written on both sides that differ) is the domain's decision, never the database's.

create function license_digits(number text) returns text
language sql
immutable
parallel safe
return nullif(
    (
        select ltrim(run[1], '0')
        from regexp_matches(number, '[0-9]+', 'g') with ordinality as r (run, position)
        order by length(run[1]) desc, position
        limit 1
    ),
    ''
);

comment on function license_digits(text) is
    'A licence number as its digits say it: the longest run of digits without leading zeros; '
    'the SQL counterpart of domain.policies.license_digits.';

create index exclusion_records_latest_license_digits_idx
    on exclusion_records (license_digits(license_number))
    where latest;
