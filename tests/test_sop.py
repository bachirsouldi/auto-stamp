"""Exercise department->folder resolution against a throwaway DB and temp folder tree."""
import os, sys, tempfile

PROJECT_ROOT = r"d:\Web\auto-stamp"
work = tempfile.mkdtemp()
os.chdir(work)
sys.path.insert(0, PROJECT_ROOT)

import database as db
import sop

root = os.path.join(work, "sops")
for d in ["Quality", "Manufacturing", "hr", "Company-Wide"]:
    os.makedirs(os.path.join(root, d))
# a stray PDF directly in root — should never be exposed
open(os.path.join(root, "loose.pdf"), "w").close()

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond: fails.append(name)

print("\n== list_subfolders ==")
subs = sop.list_subfolders(root)
check("finds all 4 dept dirs", set(subs) == {"Quality", "Manufacturing", "hr", "Company-Wide"})
check("missing root returns []", sop.list_subfolders(os.path.join(work, "nope")) == [])

print("\n== exact / case-insensitive name matching ==")
p = sop.resolve_department_folder(root, "Quality")
check("exact case match resolves", p == os.path.join(root, "Quality"))
p2 = sop.resolve_department_folder(root, "QUALITY")
check("case-insensitive match resolves", p2 == os.path.join(root, "Quality"))
p3 = sop.resolve_department_folder(root, "  hr  ")
check("whitespace-trimmed match resolves", p3 == os.path.join(root, "hr"))
check("unknown department resolves to None", sop.resolve_department_folder(root, "Nonexistent Dept") is None)
check("empty department resolves to None", sop.resolve_department_folder(root, "") is None)

print("\n== admin override (dept_folder_map) ==")
db.set_dept_folder_map("Quality Assurance", "Quality")  # GLPI entity name != folder name
p4 = sop.resolve_department_folder(root, "Quality Assurance")
check("override resolves to mapped folder", p4 == os.path.join(root, "Quality"))
db.delete_dept_folder_map("Quality Assurance")
check("override removed -> falls back to name match (fails, no exact match)",
      sop.resolve_department_folder(root, "Quality Assurance") is None)

print("\n== override cannot escape root ==")
db.set_dept_folder_map("Evil", "../../../etc")
p5 = sop.resolve_department_folder(root, "Evil")
check("override to a name not in subfolders is rejected", p5 is None)
db.delete_dept_folder_map("Evil")

print("\n== common (public) folder ==")
check("no common folder configured by default", sop.get_common_folder_name() == "")
sop.set_common_folder_name("Company-Wide")
check("common folder name persists", sop.get_common_folder_name() == "Company-Wide")

print("\n== get_visible_folders: regular user ==")
folders = sop.get_visible_folders(root, "Quality", is_admin=False)
check("sees own department folder", any(v == os.path.join(root, "Quality") for v in folders.values()))
check("sees the common folder too", any(v == os.path.join(root, "Company-Wide") for v in folders.values()))
check("does NOT see Manufacturing", not any(v == os.path.join(root, "Manufacturing") for v in folders.values()))
check("exactly 2 folders visible (dept + common)", len(folders) == 2)

print("\n== get_visible_folders: user with unmatched department ==")
folders_none = sop.get_visible_folders(root, "Nonexistent Dept", is_admin=False)
check("only common folder shown", list(folders_none.values()) == [os.path.join(root, "Company-Wide")])

print("\n== get_visible_folders: user with no department at all ==")
folders_empty = sop.get_visible_folders(root, "", is_admin=False)
check("only common folder shown for blank department", list(folders_empty.values()) == [os.path.join(root, "Company-Wide")])

print("\n== get_visible_folders: admin sees everything ==")
folders_admin = sop.get_visible_folders(root, "Quality", is_admin=True)
check("admin sees all 4 subfolders", len(folders_admin) == 4)
check("admin sees Manufacturing too", any(v == os.path.join(root, "Manufacturing") for v in folders_admin.values()))

print("\n== common folder never double-listed if it equals the dept folder ==")
sop.set_common_folder_name("Quality")
folders_dup = sop.get_visible_folders(root, "Quality", is_admin=False)
check("Quality user with common=Quality sees exactly one entry, not two",
      len(folders_dup) == 1 and list(folders_dup.values())[0] == os.path.join(root, "Quality"))
sop.set_common_folder_name("Company-Wide")

print("\n== loose root PDFs are never exposed ==")
all_paths = set()
for is_admin in (True, False):
    for dep in ("Quality", "Manufacturing", "", "Nonexistent"):
        all_paths.update(sop.get_visible_folders(root, dep, is_admin=is_admin).values())
check("root itself never appears as a visible folder", root not in all_paths)

print("\n" + ("ALL PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
