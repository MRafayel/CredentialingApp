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

## Layout

```
backend/    Python package `credentialing` (FastAPI) and its tests
frontend/   React + TypeScript UI
```
