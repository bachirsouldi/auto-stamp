"""Department-scoped SOP folder resolution.

The shared viewer folder (Admin > Shared Viewer) has one subfolder per department.
A logged-in user sees their department's subfolder plus an optional "common" folder
visible to everyone. Matching is by folder NAME, case-insensitively, with an
admin-configurable override table (database.dept_folder_map) for the cases where a
GLPI entity's name doesn't read the same as its folder — different punctuation,
abbreviations, language.

No Streamlit dependency — importable and testable on its own.
"""

import os
from typing import List, Optional

import database as db

COMMON_FOLDER_SETTING = "shared_viewer_common_subfolder"


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def folder_matches_department(folder_name: str, department: str) -> bool:
    """True if folder_name is the default (unmapped) match for department — i.e.
    same name, case/whitespace-insensitive. Does not consult dept_folder_map
    overrides; callers checking a specific admin mapping should read
    database.get_dept_folder_map() directly."""
    return bool(folder_name) and bool(department) and _normalize(folder_name) == _normalize(department)


def list_subfolders(root: str) -> List[str]:
    """Immediate subfolder names under root, or [] if root is missing/unreadable."""
    try:
        return sorted(
            d for d in os.listdir(root)
            if os.path.isdir(os.path.join(root, d))
        )
    except OSError:
        return []


def get_common_folder_name() -> str:
    """Subfolder name (if any) shown to every authenticated user regardless of
    department — e.g. company-wide policies."""
    return (db.get_setting("__system__", COMMON_FOLDER_SETTING, "") or "").strip()


def set_common_folder_name(name: str) -> None:
    db.set_setting("__system__", COMMON_FOLDER_SETTING, name.strip())


def resolve_department_folder(root: str, department: str) -> Optional[str]:
    """Return the absolute path of the subfolder matching `department`, or None.

    Resolution order: (1) an explicit override in dept_folder_map, (2) a
    case-insensitive name match against root's subfolders. Never returns a path
    outside root.
    """
    if not department or not root or not os.path.isdir(root):
        return None

    overrides = db.get_dept_folder_map()
    subfolders = list_subfolders(root)

    target_name = overrides.get(department)
    if target_name is None:
        # No override — match by normalized name.
        wanted = _normalize(department)
        target_name = next((d for d in subfolders if _normalize(d) == wanted), None)

    if target_name is None or target_name not in subfolders:
        return None

    return os.path.join(root, target_name)


def get_visible_folders(root: str, department: str, is_admin: bool = False) -> dict:
    """Folders a given user may browse: label -> absolute path.

    Admins see every subfolder (so they can verify content without a folder-name
    trick being required). Everyone else sees their department's folder (if
    resolvable) plus the common folder (if configured and present).
    """
    folders: dict = {}
    if not root or not os.path.isdir(root):
        return folders

    if is_admin:
        for d in list_subfolders(root):
            folders[f"📂 {d}"] = os.path.join(root, d)
        return folders

    dept_path = resolve_department_folder(root, department)
    if dept_path:
        folders[f"📂 {os.path.basename(dept_path)}"] = dept_path

    common_name = get_common_folder_name()
    if common_name:
        common_path = os.path.join(root, common_name)
        if os.path.isdir(common_path) and common_path != dept_path:
            folders[f"🌐 {common_name}"] = common_path

    return folders
