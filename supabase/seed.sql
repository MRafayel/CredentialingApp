-- Development seed data, loaded by `make db-reset` after all migrations.
-- Made-up tenant data only: organizations, users, rosters.
-- Real public datasets (OIG list, NPPES) are loaded by their own sync commands, not from here.

-- The made-up organization local API calls act for (header X-Organization-Id).
insert into organizations (id, name)
values ('00000000-0000-0000-0000-000000000001', 'Dev Organization')
on conflict (id) do nothing;
