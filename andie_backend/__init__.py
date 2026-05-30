"""Compatibility package for absolute imports under ``andie_backend.*``.

This repo is often executed directly from its root (without editable install),
so we include the repository root in this package search path to resolve
subpackages like ``andie_backend.interfaces`` -> ``interfaces``.
"""

from pathlib import Path

_pkg_dir = Path(__file__).resolve().parent
_repo_root = _pkg_dir.parent

# Allow importing both real children under andie_backend/ and top-level modules
# in the repository root through the andie_backend.* namespace.
__path__ = [str(_pkg_dir), str(_repo_root)]
