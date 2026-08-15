"""Exercise the reworked auth layer against a throwaway DB."""
import os, sys, tempfile, hashlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
work = tempfile.mkdtemp()
os.chdir(work)  # database.py resolves database.db relative to cwd
sys.path.insert(0, PROJECT_ROOT)

import database as db

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(name)

print("\n== password hashing ==")
h = db.hash_password("hunter2")
check("hash is pbkdf2 format", h.startswith("pbkdf2_sha256$"))
check("hash is salted (differs per call)", h != db.hash_password("hunter2"))
check("verify correct password", db.verify_password("hunter2", h))
check("reject wrong password", not db.verify_password("hunter3", h))
check("reject empty stored", not db.verify_password("hunter2", ""))

print("\n== legacy hash compatibility ==")
legacy = hashlib.sha256(b"oldpass").hexdigest()
check("verify legacy sha256", db.verify_password("oldpass", legacy))
check("reject wrong vs legacy", not db.verify_password("nope", legacy))
check("legacy needs rehash", db._needs_rehash(legacy))
check("current does not need rehash", not db._needs_rehash(h))

print("\n== seeded admin login ==")
check("default admin authenticates", db.authenticate_user("admin", "admin123") is not None)
check("wrong password rejected", db.authenticate_user("admin", "wrong") is None)
check("unknown user rejected", db.authenticate_user("ghost", "admin123") is None)

print("\n== legacy password upgraded in place on login ==")
conn = db.get_connection(); cur = conn.cursor()
cur.execute("INSERT INTO users (username, password, is_admin) VALUES (?,?,?)",
            ("legacyuser", legacy, 0))
conn.commit(); conn.close()
check("legacy user can log in", db.authenticate_user("legacyuser", "oldpass") is not None)
conn = db.get_connection(); cur = conn.cursor()
cur.execute("SELECT password FROM users WHERE username='legacyuser'")
stored = cur.fetchone()[0]; conn.close()
check("hash upgraded to pbkdf2", stored.startswith("pbkdf2_sha256$"))
check("still logs in after upgrade", db.authenticate_user("legacyuser", "oldpass") is not None)

print("\n== sessions ==")
tok = db.create_session("admin")
check("token is long/random", len(tok) >= 40)
check("token resolves to user", db.get_user_by_session(tok) == "admin")
check("EMPTY token rejected (old bypass)", db.get_user_by_session("") is None)
check("None token rejected", db.get_user_by_session(None) is None)
check("short token rejected", db.get_user_by_session("x") is None)
check("forged token rejected", db.get_user_by_session("z" * 43) is None)

conn = db.get_connection(); cur = conn.cursor()
cur.execute("SELECT token_hash FROM sessions WHERE username='admin'")
stored_tok = cur.fetchone()[0]; conn.close()
check("raw token NOT stored in DB", stored_tok != tok)
check("stored value is the token hash", stored_tok == hashlib.sha256(tok.encode()).hexdigest())

print("\n== session invalidation ==")
t2 = db.create_session("admin")
db.destroy_session(t2)
check("destroyed session rejected", db.get_user_by_session(t2) is None)

t3, t4 = db.create_session("admin"), db.create_session("admin")
check("multiple concurrent sessions work", db.get_user_by_session(t3) == "admin"
                                      and db.get_user_by_session(t4) == "admin")
db.destroy_user_sessions("admin")
check("bulk revoke kills all", db.get_user_by_session(t3) is None
                          and db.get_user_by_session(t4) is None)

print("\n== expiry ==")
t5 = db.create_session("admin", ttl_hours=0)
check("zero-TTL session already expired", db.get_user_by_session(t5) is None)

print("\n== password change revokes sessions ==")
t6 = db.create_session("legacyuser")
db.change_password("legacyuser", "brandnew")
check("old session revoked after pw change", db.get_user_by_session(t6) is None)
check("new password works", db.authenticate_user("legacyuser", "brandnew") is not None)
check("old password rejected", db.authenticate_user("legacyuser", "oldpass") is None)

print("\n== user deletion cleans sessions ==")
db.create_user("tmpuser", "pw1234", False)
t7 = db.create_session("tmpuser")
db.delete_user("tmpuser")
check("session gone after delete", db.get_user_by_session(t7) is None)

print("\n== legacy settings-based tokens purged ==")
db.set_setting("admin", "session_token", "legacy-token-value")
db.init_db()
conn = db.get_connection(); cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM settings WHERE setting_key='session_token'")
n = cur.fetchone()[0]; conn.close()
check("old session_token rows removed on init", n == 0)

print("\n" + ("ALL PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
