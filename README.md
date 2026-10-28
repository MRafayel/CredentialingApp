# Credentialing

A provider credential verification assistant for U.S. healthcare credentialing teams: exclusion
checks, NPI lookup, state license and board certification verification, with every result
traceable to its source and the time it was verified.

## Get started

Requires Python 3.14, Node 24, pnpm and make.

```sh
make setup    # creates CredentialingVenv, installs locked dependencies, writes .env, enables git hooks
make api      # API on http://127.0.0.1:8000  (docs at /docs when running locally)
make web      # UI on http://localhost:5173
make check    # lint, architecture contracts, types, tests, UI build
```

`make help` lists every command.

Local data comes from the public sources themselves, never from the repository:

```sh
make db-start && make db-reset    # local Postgres with the schema and a made-up dev organization
make sync SOURCE=oig_leie         # download the federal exclusion list (SOURCE=all for every source)
make sources                      # every source's links, last fetch, data age and next fetch (ARGS=--json)
```

To keep the lists current, run `make sync-due` on a schedule. It fetches only the sources whose
poll interval has elapsed since their last completed fetch (a failed source is retried on every
run), so a daily cron entry is enough:

```sh
0 6 * * * cd /path/to/CredentialingApp && make sync-due
```

Until authentication is in place, local API calls name their organization in a header. The
seeded development organization is `00000000-0000-0000-0000-000000000001`:

```sh
curl -X POST localhost:8000/providers -H 'X-Organization-Id: 00000000-0000-0000-0000-000000000001' \
  -H 'content-type: application/json' \
  -d '{"entity_kind": "individual", "person_name": {"last": "Sampleperson", "first": "Avery"}}'
```

The header is accepted only when `CREDENTIALING_ENVIRONMENT=local`. The UI sends it for the
organization in `VITE_ORGANIZATION_ID` (default: the seeded one); copy `frontend/.env.example`
to `frontend/.env.local` to change it.

The UI's API types are generated from the backend's OpenAPI schema. After changing an endpoint:

```sh
make api-types    # regenerate frontend/src/lib/api-schema.d.ts (make lint fails while it is stale)
make test-web     # frontend unit tests (or `pnpm test` inside frontend/)
```

## Layout

```
backend/    Python package `credentialing` (FastAPI) and its tests
frontend/   React + TypeScript UI
```
