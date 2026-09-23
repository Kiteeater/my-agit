"""Deprecated re-export of project, version, and release rules.

Import from agit.biz instead. This module remains so existing callers of
agit.core keep working.
"""

import warnings

from agit.biz.project import create_project, get_project
from agit.biz.release import (
    bootstrap_black,
    compare_red_black,
    gate_passes,
    list_releases,
    mark_red,
    run_gate,
)
from agit.biz.version import get_version, list_versions, push_version, version_id_for

warnings.warn(
    "agit.core is deprecated; import from agit.biz",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "bootstrap_black",
    "compare_red_black",
    "create_project",
    "gate_passes",
    "get_project",
    "get_version",
    "list_releases",
    "list_versions",
    "mark_red",
    "push_version",
    "run_gate",
    "version_id_for",
]
