"""Connectors: replaceable adapters to external credential sources (OIG, NPPES, state boards...).

One source = one subpackage. A connector turns a source's raw answer into normalized domain
models with full provenance; nothing source-specific leaks past this package. Shared connector
machinery (HTTP, retries, rate limiting, instrumentation, registry) lives here once, never
copy-pasted per source.

May import: ``credentialing.domain`` and ``credentialing.application``. Connectors never import
each other, ``api`` or ``infrastructure``.
"""
