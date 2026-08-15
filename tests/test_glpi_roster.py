"""Exercise database.py's GLPI-roster functions (provisioning, bulk sync, dept map)."""
import os, sys, tempfile

PROJECT_ROOT = r"d:\Web\auto-stamp"
work = tempfile.mkdtemp()
os.chdir(work)
sys.path.insert(0, PROJECT_ROOT)

import database as db

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond: fails.append(name)

print("\n== provision_glpi_user: first login creates the roster row ==")
row = db.provision_glpi_user("jdoe", "Jane Doe", "Quality", 42)
check("username stored", row["username"] == "jdoe")
check("realname stored", row["realname"] == "Jane Doe")
check("department stored", row["department"] == "Quality")
check("glpi_id stored", row["glpi_id"] == 42)
check("auth_source is glpi", row["auth_source"] == db.AUTH_GLPI)
check("password is the unusable sentinel", row["password"] == db.UNUSABLE_PASSWORD)
check("not admin by default", not row["is_admin"])
check("active by default", bool(row["is_active"]))

print("\n== unusable password never authenticates ==")
check("cannot log in with an empty password", not db.verify_password("", db.UNUSABLE_PASSWORD))
check("cannot log in with the literal sentinel as password", not db.verify_password("!", db.UNUSABLE_PASSWORD))
check("cannot log in with any guess", not db.verify_password("hunter2", db.UNUSABLE_PASSWORD))
check("authenticate_user rejects it too", db.authenticate_user("jdoe", "anything") is None)

print("\n== provision_glpi_user: second login refreshes department, keeps identity ==")
row2 = db.provision_glpi_user("jdoe", "Jane Doe", "Manufacturing", 42)
check("department updates on re-login", row2["department"] == "Manufacturing")
check("still not admin (never silently promoted)", not row2["is_admin"])

print("\n== admin flag survives GLPI re-provisioning ==")
db.set_admin("jdoe", True)
row3 = db.provision_glpi_user("jdoe", "Jane Doe", "Quality", 42)
check("admin flag is a local decision, GLPI login doesn't reset it", bool(row3["is_admin"]))

print("\n== provisioning REFUSES to annex a same-named LOCAL account ==")
db.create_user("localbob", "s3cret!", True)  # a local admin, same shape as the seeded fallback
before = db.get_user_by_username("localbob")
threw = False
try:
    db.provision_glpi_user("localbob", "Bob GLPI", "IT", 99)
except db.LocalAccountConflictError:
    threw = True
check("raises LocalAccountConflictError instead of converting", threw)
after = db.get_user_by_username("localbob")
check("password untouched", before["password"] == after["password"])
check("auth_source still local (not silently flipped to glpi)", after["auth_source"] == db.AUTH_LOCAL)
check("still admin", bool(after["is_admin"]))
check("local password still works", db.authenticate_user("localbob", "s3cret!") is not None)
# This guards the disaster-recovery path directly: a GLPI instance's own admin-named
# superuser authenticating here must never be able to assume this app's local admin
# identity, and must never strand the real local admin by flipping their auth_source.

print("\n== sync_glpi_roster: bulk import creates + updates ==")
db.delete_user("jdoe"); db.delete_user("localbob")
db.create_user("localadmin", "adminpass", True)  # pre-existing local account

roster = [
    {"username": "alice", "realname": "Alice A", "department": "Quality", "is_active": True},
    {"username": "bob",   "realname": "Bob B",   "department": "HR",      "is_active": True},
    {"username": "localadmin", "realname": "Should Not Overwrite", "department": "IT", "is_active": True},
]
stats = db.sync_glpi_roster(roster)
check("2 new users created", stats["created"] == 2)
check("0 updated on first import", stats["updated"] == 0)
check("localadmin reported as skipped (local account)", "localadmin" in stats["skipped_local"])

la = db.get_user_by_username("localadmin")
check("localadmin's password untouched", db.authenticate_user("localadmin", "adminpass") is not None)
check("localadmin's department untouched (still None/empty)", not la["department"])
check("localadmin still admin", bool(la["is_admin"]))

alice = db.get_user_by_username("alice")
check("alice created with department", alice["department"] == "Quality")
check("alice is glpi-sourced", alice["auth_source"] == db.AUTH_GLPI)

print("\n== sync_glpi_roster: re-import updates existing GLPI users ==")
roster2 = [
    {"username": "alice", "realname": "Alice A", "department": "Regulatory", "is_active": True},
    {"username": "bob",   "realname": "Bob B",   "department": "HR",         "is_active": True},
]
stats2 = db.sync_glpi_roster(roster2)
check("2 updated, 0 created on re-import", stats2["updated"] == 2 and stats2["created"] == 0)
check("alice's department changed", db.get_user_by_username("alice")["department"] == "Regulatory")

print("\n== sync_glpi_roster: users absent from a new pull are deactivated, not deleted ==")
roster3 = [
    {"username": "alice", "realname": "Alice A", "department": "Regulatory", "is_active": True},
    # bob is gone from this pull -> left GLPI or left the entity
]
stats3 = db.sync_glpi_roster(roster3)
check("1 user deactivated", stats3["deactivated"] == 1)
bob = db.get_user_by_username("bob")
check("bob's row still exists (not deleted)", bob is not None)
check("bob is now inactive", not bob["is_active"])
check("alice remains active", bool(db.get_user_by_username("alice")["is_active"]))

print("\n== sync_glpi_roster: is_active=False in the pull deactivates directly ==")
roster4 = [{"username": "alice", "realname": "Alice A", "department": "Regulatory", "is_active": False}]
db.sync_glpi_roster(roster4)
check("alice deactivated via explicit is_active=False", not db.get_user_by_username("alice")["is_active"])

print("\n== get_all_departments ==")
depts = db.get_all_departments()
check("distinct, non-empty departments only", "Regulatory" in depts and "" not in depts and None not in depts)

print("\n== dept_folder_map CRUD ==")
db.set_dept_folder_map("Quality Assurance", "QA")
m = db.get_dept_folder_map()
check("mapping stored", m.get("Quality Assurance") == "QA")
db.set_dept_folder_map("Quality Assurance", "QualityAssurance2")
check("mapping upserts (overwrites) on same department", db.get_dept_folder_map()["Quality Assurance"] == "QualityAssurance2")
db.delete_dept_folder_map("Quality Assurance")
check("mapping removable", "Quality Assurance" not in db.get_dept_folder_map())

print("\n" + ("ALL PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
