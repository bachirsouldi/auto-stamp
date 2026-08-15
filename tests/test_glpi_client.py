"""Exercise glpi.py's DB-backed client against a fake pymysql connection — no live
GLPI/MySQL needed. Uses real bcrypt to prove the PHP '$2y$' hash format actually
verifies, since that normalization is the one place a silent bug would be dangerous
(wrongly accepting or rejecting every login)."""
import os, sys, tempfile
from unittest import mock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
work = tempfile.mkdtemp()
os.chdir(work)  # database.py resolves database.db relative to cwd
sys.path.insert(0, PROJECT_ROOT)

import bcrypt as real_bcrypt
import glpi

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond: fails.append(name)


def php_style_hash(password: str) -> str:
    """A real bcrypt hash, re-marked '$2y$' the way PHP's password_hash() does."""
    h = real_bcrypt.hashpw(password.encode(), real_bcrypt.gensalt(rounds=10)).decode()
    return "$2y$" + h[4:]  # bcrypt's own gensalt marks '$2b$'; swap to PHP's marker


class FakeCursor:
    """Mimics a pymysql DictCursor: scripted responses keyed by a substring of the
    SQL executed, in call order per key."""
    def __init__(self, script):
        self.script = dict(script)  # {sql_substring: [row_or_rows, ...]}
        self.last_result = None
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        for key, results in self.script.items():
            if key in sql:
                self.last_result = results.pop(0) if isinstance(results, list) else results
                return
        raise AssertionError(f"No scripted response for SQL: {sql[:80]}")
    def fetchone(self):
        return self.last_result
    def fetchall(self):
        return self.last_result


class FakeConnection:
    def __init__(self, script):
        self.script = script
        self.closed = False
    def cursor(self):
        return FakeCursor(self.script)
    def close(self):
        self.closed = True


def configure(host="dbhost", port=3306, database="glpi", user="ro_user",
              password="pw", enabled=True, ssl=True):
    glpi.set_config(host=host, port=port, database=database, user=user,
                     password=password, enabled=enabled, ssl=ssl)


print("\n== configuration plumbing ==")
check("disabled with nothing configured", not glpi.is_enabled())
configure()
check("enabled once host+db+user+enabled=True are set", glpi.is_enabled())
check("TLS on -> not flagged unencrypted", not glpi.uses_unencrypted_db_connection())
configure(ssl=False)
check("TLS off -> flagged unencrypted", glpi.uses_unencrypted_db_connection())
configure(ssl=True)

print("\n== bcrypt verification: real PHP-style ($2y$) hash ==")
real_hash = php_style_hash("hunter2")
check("hash actually uses PHP's $2y$ marker", real_hash.startswith("$2y$"))
check("correct password verifies", glpi._verify_glpi_password("hunter2", real_hash))
check("wrong password rejected", not glpi._verify_glpi_password("wrongpass", real_hash))
check("empty stored hash rejected", not glpi._verify_glpi_password("hunter2", ""))
check("non-bcrypt hash (e.g. legacy/LDAP placeholder) rejected, not crashed",
      not glpi._verify_glpi_password("hunter2", "not-a-bcrypt-hash"))
check("phpass-style hash ($P$) rejected, not crashed",
      not glpi._verify_glpi_password("hunter2", "$P$Bxyz1234567890abcdefghijklmnopqr"))

print("\n== authenticate(): correct credentials, active user ==")
user_hash = php_style_hash("correcthorse")

def make_success_script():
    """Fresh script dict each call — FakeCursor pops from these lists, so a script
    used by one authenticate() call is exhausted and must never be reused."""
    return {
        "FROM glpi_users WHERE name": [{
            "id": 42, "name": "jdoe", "password": user_hash,
            "firstname": "Jane", "realname": "Doe", "is_active": 1, "is_deleted": 0,
        }],
        "FROM glpi_profiles_users": [{"entity_id": 7, "entity_name": "Quality"}],
    }

def make_wrong_pw_script():
    return {
        "FROM glpi_users WHERE name": [{
            "id": 42, "name": "jdoe", "password": user_hash,
            "firstname": "Jane", "realname": "Doe", "is_active": 1, "is_deleted": 0,
        }],
    }

with mock.patch("glpi._connect", return_value=FakeConnection(make_success_script())):
    gu = glpi.authenticate("jdoe", "correcthorse")
check("GlpiUser returned", gu is not None)
check("username matches", gu.username == "jdoe")
check("realname assembled", gu.realname == "Jane Doe")
check("glpi_id is the row id", gu.glpi_id == 42)
check("department resolved from entity join", gu.department == "Quality")
check("department_id resolved", gu.department_id == 7)

print("\n== authenticate(): wrong password ==")
with mock.patch("glpi._connect", return_value=FakeConnection(make_wrong_pw_script())):
    result = glpi.authenticate("jdoe", "totallywrong")
check("wrong password -> None, not an exception", result is None)

print("\n== authenticate(): unknown username ==")
script3 = {"FROM glpi_users WHERE name": [None]}
with mock.patch("glpi._connect", return_value=FakeConnection(script3)):
    result = glpi.authenticate("ghost", "anything")
check("unknown username -> None", result is None)

print("\n== authenticate(): inactive / deleted accounts rejected even with correct password ==")
for flag_name, row in [
    ("is_active=0", {"id": 1, "name": "x", "password": user_hash, "firstname": "", "realname": "",
                      "is_active": 0, "is_deleted": 0}),
    ("is_deleted=1", {"id": 1, "name": "x", "password": user_hash, "firstname": "", "realname": "",
                       "is_active": 1, "is_deleted": 1}),
]:
    with mock.patch("glpi._connect", return_value=FakeConnection({"FROM glpi_users WHERE name": [row]})):
        result = glpi.authenticate("x", "correcthorse")
    check(f"{flag_name} rejects login despite correct password", result is None)

print("\n== authenticate(): user with no entity assignment ==")
script4 = {
    "FROM glpi_users WHERE name": [{
        "id": 5, "name": "noentity", "password": user_hash,
        "firstname": "No", "realname": "Entity", "is_active": 1, "is_deleted": 0,
    }],
    "FROM glpi_profiles_users": [None],
}
with mock.patch("glpi._connect", return_value=FakeConnection(script4)):
    gu = glpi.authenticate("noentity", "correcthorse")
check("login still succeeds with no entity", gu is not None)
check("department is empty string, not None/crash", gu.department == "")
check("department_id is None", gu.department_id is None)

print("\n== authenticate(): DB unreachable raises GlpiError, not silent failure ==")
import pymysql

# _connect() itself is the layer that turns a raw pymysql.MySQLError into GlpiError
# (see glpi._connect) — so exercise that translation for real, via pymysql.connect,
# rather than assuming _connect's error handling and mocking around it.
def raise_pymysql_connect_error(*a, **kw):
    raise pymysql.MySQLError("Connection refused")
with mock.patch("glpi.pymysql.connect", side_effect=raise_pymysql_connect_error):
    threw = False
    try:
        glpi.authenticate("jdoe", "correcthorse")
    except glpi.GlpiError as e:
        threw = True
        conn_err_msg = str(e)
check("connection failure raises GlpiError (caller can fall back to local auth)", threw)
check("error message names the host, not just a raw exception dump", "dbhost" in conn_err_msg)

print("\n== authenticate(): connection is always closed ==")
conn = FakeConnection(make_success_script())
with mock.patch("glpi._connect", return_value=conn):
    glpi.authenticate("jdoe", "correcthorse")
check("connection closed after a successful auth", conn.closed)

conn2 = FakeConnection(make_wrong_pw_script())
with mock.patch("glpi._connect", return_value=conn2):
    glpi.authenticate("jdoe", "wrongpass")
check("connection closed after a rejected auth too", conn2.closed)

print("\n== fetch_users(): roster pull with joined department ==")
roster_rows = [
    {"username": "alice", "firstname": "A", "realname": "Aa", "is_active": 1, "is_deleted": 0, "department": "Quality"},
    {"username": "bob",   "firstname": "B", "realname": "Bb", "is_active": 0, "is_deleted": 0, "department": None},
    {"username": "carol", "firstname": "C", "realname": "Cc", "is_active": 1, "is_deleted": 1, "department": "IT"},
]
# FakeCursor's script convention: each value is a queue of per-execute() responses.
# fetchall() needs the whole row-list as ONE queued response, so it must be wrapped
# in an extra list — [roster_rows], not roster_rows directly (which would instead be
# read as "3 separate single-row responses" and break on the first fetchall()).
roster_script = {"FROM glpi_users u": [roster_rows]}
with mock.patch("glpi._connect", return_value=FakeConnection(roster_script)):
    users = glpi.fetch_users()
check("all 3 rows returned", len(users) == 3)
check("realname assembled per row", next(u for u in users if u["username"] == "alice")["realname"] == "A Aa")
check("NULL department becomes empty string, not None", next(u for u in users if u["username"] == "bob")["department"] == "")
check("is_active=0 -> False", next(u for u in users if u["username"] == "bob")["is_active"] is False)
check("is_deleted=1 overrides is_active=1 -> False", next(u for u in users if u["username"] == "carol")["is_active"] is False)
check("active+not-deleted -> True", next(u for u in users if u["username"] == "alice")["is_active"] is True)

print("\n== test_connection(): reachable and schema present ==")
tc_script = {"FROM glpi_users": [{"n": 1000}], "FROM glpi_entities": [{"n": 12}]}
with mock.patch("glpi._connect", return_value=FakeConnection(tc_script)):
    ok, msg = glpi.test_connection()
check("reports success", ok)
check("message includes counts", "1000" in msg and "12" in msg)

print("\n== test_connection(): host reachable but schema missing ==")
class BrokenCursor(FakeCursor):
    def execute(self, sql, params=None):
        raise pymysql.MySQLError("Table 'glpi.glpi_users' doesn't exist")
class BrokenConn(FakeConnection):
    def cursor(self):
        return BrokenCursor({})
with mock.patch("glpi._connect", return_value=BrokenConn({})):
    ok, msg = glpi.test_connection()
check("reports failure with a schema hint, not a raw traceback", not ok and "SELECT" in msg)

print("\n== test_connection(): missing config fields caught before connecting ==")
configure(host="", database="", user="")
ok, msg = glpi.test_connection()
check("incomplete config rejected without attempting a connection", not ok)

print("\n" + ("ALL PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
