"""Server-side path safety for user-supplied export destinations.

Users type destination folders by hand in the Stamp and Read-Only tools, so every
write is constrained to an admin-configured allowlist (Admin > Export Paths).
An empty allowlist means unrestricted, which is the pre-existing behaviour.

No Streamlit dependency — importable and testable on its own.
"""

import os
from typing import List

import database as db

SETTING_KEY = "export_allowed_roots"


def get_export_roots() -> List[str]:
    """Allowed root folders for server-side writes. Empty list = unrestricted."""
    raw = db.get_setting("__system__", SETTING_KEY, "") or ""
    roots = []
    for line in raw.replace(";", "\n").splitlines():
        line = line.strip()
        if line:
            roots.append(os.path.realpath(os.path.abspath(os.path.expanduser(line))))
    return roots


def is_path_allowed(path: str, roots: List[str] | None = None) -> bool:
    """True if path sits inside one of the configured export roots."""
    if roots is None:
        roots = get_export_roots()
    if not roots:
        return True
    target = os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    for root in roots:
        try:
            if os.path.commonpath([target, root]) == root:
                return True
        except ValueError:
            continue  # different drives on Windows
    return False


def resolve_export_path(p_raw: str, default_name: str) -> str:
    """Normalize a user-supplied destination and enforce the export allowlist.

    Returns the absolute file path to write. Raises ValueError if the path escapes
    the configured roots or no destination was given.
    """
    if not p_raw or not p_raw.strip():
        raise ValueError("No destination path provided.")

    p = os.path.abspath(os.path.expanduser(p_raw.strip()))

    # Treat the default name as a bare filename: a value like "..\\..\\evil.pdf"
    # must not climb out of the chosen directory.
    safe_name = os.path.basename(default_name or "output.pdf") or "output.pdf"

    if os.path.isdir(p) or p_raw.strip().endswith(("/", "\\")):
        p = os.path.join(p, safe_name)

    p = os.path.normpath(p)

    roots = get_export_roots()
    if not is_path_allowed(p, roots):
        raise ValueError(
            "Destination is outside the allowed export folders. "
            "Permitted roots: " + ", ".join(roots)
        )
    return p


def get_subdirectories(path: str) -> List[str]:
    """List browsable subdirectories, hiding anything outside the allowlist."""
    try:
        p = os.path.normpath(os.path.expanduser(path))
        if not os.path.isdir(p):
            return []
        roots = get_export_roots()
        return [
            d for d in os.listdir(p)
            if os.path.isdir(os.path.join(p, d))
            and is_path_allowed(os.path.join(p, d), roots)
        ]
    except OSError:
        return []
