"""Package-data directory for ``flowstate init`` templates.

This module intentionally contains no code. Its sole purpose is to mark
``flowstate.init_templates`` as an importable package so
``importlib.resources.files("flowstate.init_templates")`` resolves both from
a source checkout and from an installed wheel. The template files
themselves (``flowstate.toml.tmpl`` and ``example_*.flow``) live next to
this file and are shipped as package data via the hatchling wheel target
configured in ``pyproject.toml``.
"""
