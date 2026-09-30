"""Infrastructure: our own technical plumbing.

Configuration, logging, persistence (repository implementations), background jobs. Implements
domain ports; contains no business rules.

May import: ``credentialing.domain`` and ``credentialing.application``. Never ``api`` or
``connectors``.
"""
