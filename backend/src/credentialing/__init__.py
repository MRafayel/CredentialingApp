"""Healthcare provider credential verification platform.

A modular monolith. Dependencies point inward only:

    main -> api | infrastructure | connectors -> application -> domain

The rules are enforced by Import Linter (see ``[tool.importlinter]`` in pyproject.toml).
"""
