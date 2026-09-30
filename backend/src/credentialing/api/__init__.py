"""API layer: HTTP in, HTTP out.

FastAPI routers, request/response schemas, auth and error mapping. Route handlers are thin:
validate, call one use case, map the result. No business logic here.

May import: ``credentialing.application`` and ``credentialing.domain``. Never ``infrastructure``
or ``connectors``; concrete implementations are injected by ``credentialing.main``.
"""
