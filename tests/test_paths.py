"""Exercise the export-path allowlist against a throwaway DB."""
import os, sys, tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
work = tempfile.mkdtemp()
os.chdir(work)  # database.py resolves database.db relative to cwd
sys.path.insert(0, PROJECT_ROOT)

import database as db
import pathsafe

allowed = os.path.join(work, "exports")
other   = os.path.join(work, "secrets")
nested  = os.path.join(allowed, "sub", "deep")
os.makedirs(nested); os.makedirs(other)

fails = []
def check(name, cond):
    print(("  PASS  " if cond else "  FAIL  ") + name)
    if not cond:
        fails.append(name)

def denied(raw, name="out.pdf"):
    try:
        pathsafe.resolve_export_path(raw, name)
        return False
    except ValueError:
        return True

print("\n== unrestricted mode (no roots configured) ==")
db.set_setting("__system__", "export_allowed_roots", "")
check("empty config = unrestricted", pathsafe.get_export_roots() == [])
check("arbitrary path allowed", not denied(other))
check("directory gets default filename appended",
      pathsafe.resolve_export_path(allowed, "x.pdf") == os.path.join(allowed, "x.pdf"))

print("\n== restricted mode ==")
db.set_setting("__system__", "export_allowed_roots", allowed)
check("one root parsed", len(pathsafe.get_export_roots()) == 1)
check("write inside root allowed", not denied(os.path.join(allowed, "a.pdf")))
check("write in nested subdir allowed", not denied(os.path.join(nested, "a.pdf")))
check("write to root dir itself allowed", not denied(allowed))

print("\n== traversal / escape attempts blocked ==")
check("sibling dir blocked", denied(os.path.join(other, "a.pdf")))
check("..\\ traversal blocked", denied(os.path.join(allowed, "..", "secrets", "a.pdf")))
check("deep ..\\..\\ traversal blocked", denied(os.path.join(nested, "..", "..", "..", "secrets", "x.pdf")))
check("windows system dir blocked", denied(r"C:\Windows\System32\evil.pdf"))
check("home dir blocked", denied("~/evil.pdf"))
check("empty path rejected", denied(""))
check("whitespace path rejected", denied("   "))

print("\n== malicious default filename cannot escape ==")
p = pathsafe.resolve_export_path(allowed, r"..\..\secrets\evil.pdf")
check("filename traversal stripped to basename", p == os.path.join(allowed, "evil.pdf"))
check("result stays inside root", pathsafe.is_path_allowed(p))
p2 = pathsafe.resolve_export_path(allowed, "../../etc/passwd.pdf")
check("posix-style filename traversal stripped", p2 == os.path.join(allowed, "passwd.pdf"))

print("\n== multiple roots ==")
db.set_setting("__system__", "export_allowed_roots", allowed + "\n" + other)
check("both roots parsed", len(pathsafe.get_export_roots()) == 2)
check("first root allowed", not denied(os.path.join(allowed, "a.pdf")))
check("second root allowed", not denied(os.path.join(other, "a.pdf")))
check("outside both still blocked", denied(os.path.join(work, "elsewhere", "a.pdf")))

db.set_setting("__system__", "export_allowed_roots", allowed + ";" + other)
check("semicolon separator also parsed", len(pathsafe.get_export_roots()) == 2)

print("\n== prefix-collision is not a bypass ==")
sneaky = allowed + "_evil"
os.makedirs(sneaky, exist_ok=True)
db.set_setting("__system__", "export_allowed_roots", allowed)
check("'exports_evil' not treated as inside 'exports'", denied(os.path.join(sneaky, "a.pdf")))

print("\n== folder picker hides disallowed dirs ==")
subs = pathsafe.get_subdirectories(work)
check("picker hides sibling outside root", "secrets" not in subs)
check("picker shows the allowed root", "exports" in subs)
check("picker lists children of allowed root", "sub" in pathsafe.get_subdirectories(allowed))
check("missing dir returns empty", pathsafe.get_subdirectories(os.path.join(work, "nope")) == [])

print("\n" + ("ALL PASSED" if not fails else "FAILURES: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
