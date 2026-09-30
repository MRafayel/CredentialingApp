"""Application layer: use cases.

Orchestrates the domain to fulfil one user intent (resolve a provider, run a verification plan,
refresh a stale credential). Talks to the outside world only through domain ports.

May import: ``credentialing.domain``. No frameworks, no I/O libraries.
"""
