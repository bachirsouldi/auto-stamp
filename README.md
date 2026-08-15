# 🛠 Advanced PDF Watermark & Stamp Tool

A Streamlit application for stamping, watermarking, and managing PDF documents,
with department-scoped SOP distribution and optional GLPI-backed login.

---

## ✨ Key Features

### 🎯 Stamping / Watermarking
- Image and text stamps with position (mm), size, rotation, opacity, border, and padding
- Tiled diagonal watermarks (text)
- Multiple stamps per document, all edited in one **Stamp Inspector** — no hunting
  across panels for the control that changes what you're looking at
- Per-stamp page ranges
- Optional digital-signature stamp (name + timestamp, finalized at Apply/Print time)
- Live preview, up to 100 pages, rendered on demand (only the page you're viewing
  is rasterized, so quality and page count don't cost you a slow first load)

### 📄 Document Tools
| Tab | Purpose |
|---|---|
| 📋 SOPs | Department-scoped document viewer (see below) |
| 🪶 Watermark / Stamp | The stamping editor above |
| 🔗 Merge PDFs | Combine multiple PDFs |
| ✂️ Split PDF | Split by page range |
| 📄 Extract Pages | Pull a page range into a new file |
| 🔍 Extract Text | OCR-backed text extraction, multi-language, auto-rotation |
| 🖼 Convert to Images | Render pages to PNG/JPEG |
| 📦 Compress PDF | Reduce file size |
| 🔒 Read Only | AES-256 lockdown or image-flatten, single or bulk |
| 🔑 Protected Viewer | Lock/view PDFs with an app-only password |
| 🔎 Compare PDFs | Grid heatmap, Pantone/colour analysis, alignment-transform detection |

Which tabs and buttons a user sees is governed by the permission system in
Admin ▸ Permissions.

### 👤 Access Control
- Username/password login, PBKDF2-hashed, salted; short-lived hashed session tokens
- Optional **GLPI-backed login** — see the dedicated section below
- Per-department SOP folder scoping
- Admin dashboard: connected users, permissions, user management, shared-viewer
  folder, SOP department mapping, GLPI integration, printing, export-path allowlist

---

## 🚀 Installation

```bash
pip install -r requirements.txt
```

Requires Tesseract OCR on the server if you use the Extract Text tab's OCR path
(the app looks for `C:\Program Files\Tesseract-OCR\tesseract.exe` on Windows by
default; without it, OCR features are simply unavailable, everything else still
works).

### ▶ Run

```bash
streamlit run index.py
```

`run_dev.bat` and `run_prod.bat` are provided for local (`localhost:8501`) and
production (behind the configured domain, port 443) launches.

### First login

A default administrator account is seeded on first run:

```
username: admin
password: admin123
```

**Change this password immediately** (🔑 Change Password in the top bar) — it's a
well-known default the moment this app is deployed anywhere reachable.

---

## 📁 Department-Scoped SOPs

The Shared Viewer folder (Admin ▸ Shared Viewer) holds one subfolder per
department. A logged-in user sees only their own department's subfolder — matched
against their account's `department` field — plus an optional public/common
subfolder visible to everyone. Admins see every subfolder.

A folder that isn't reachable before login has no way to be scoped by department,
since an anonymous visitor has no identity — so anything department-specific
requires logging in. The public (no-login) page shows only the configured common
folder, if any.

- **Admin ▸ Shared Viewer** — set the root folder and the public/common subfolder name.
- **Admin ▸ SOP Departments** — folders are matched to departments by name
  (case/spacing-insensitive) automatically; add an override here only when a
  department's name doesn't match its folder name.

Department comes either from a user's GLPI entity (see below) or can be set by
hand per local account.

---

## 🌐 GLPI Integration

Login can be delegated to GLPI, so staff use their existing GLPI username and
password here, and their department is pulled from their GLPI entity automatically.

**This talks directly to GLPI's own database (read-only), not GLPI's REST API.**
No API needs to be enabled on the GLPI side, and no separate token has to be
generated or rotated — only a scoped, read-only database credential.

### 1. Create a read-only database account on GLPI's database

Run this against GLPI's MySQL/MariaDB server (adjust the username, password, and
host restriction to your environment — `'%'` below means "any host," which you
should narrow to this app server's actual address):

```sql
CREATE USER 'autostamp_ro'@'%' IDENTIFIED BY '...';
GRANT SELECT ON glpi.glpi_users TO 'autostamp_ro'@'%';
GRANT SELECT ON glpi.glpi_profiles_users TO 'autostamp_ro'@'%';
GRANT SELECT ON glpi.glpi_entities TO 'autostamp_ro'@'%';
```

This account only ever needs `SELECT` on those three tables. Nothing in this app
writes to GLPI's database.

- Replace `glpi` in `glpi.glpi_users` etc. with your actual GLPI database name if
  it differs.
- Narrow `'autostamp_ro'@'%'` to a specific host/IP (e.g.
  `'autostamp_ro'@'10.0.0.15'`) instead of leaving it open to any host.
- If this app server and the GLPI database server aren't already on a network
  segment you trust end-to-end, put the connection behind TLS — every login sends
  a password across this link as part of the check, so it deserves the same
  protection an HTTPS login page would get.

### 2. Configure it in this app

**Admin ▸ GLPI**:
- Database host, port, database name
- The `autostamp_ro` username and password from step 1
- Require TLS (recommended — see above)
- Enable GLPI login

Click **Test Connection** — it confirms the database is reachable and that
`glpi_users` / `glpi_entities` are visible with this account's permissions.

### 3. Verify before rolling out

Use **Verify a Department Lookup** (same tab) to check a handful of real GLPI
usernames — no password required — and confirm the department this app resolves
for them matches what you expect. This catches a schema or entity-assignment
mismatch before 1000 people try to log in.

Department resolution takes a user's **lowest-id** entity assignment when they have
more than one in GLPI (`glpi_profiles_users`). If your users are single-entity,
this is exactly right. If not, the lookup tool above will show it immediately —
the fix is either a mapping override (Admin ▸ SOP Departments) or a one-line query
change, not a rebuild.

### 4. Populate the roster

Either let it happen automatically — a user's department/name is refreshed every
time they log in — or click **Import Roster from GLPI** (same tab) to pull and
tag everyone in one pass, including marking anyone no longer in GLPI as inactive.

### Notes

- Passwords are **never** copied into this app. Every GLPI-backed login checks the
  live password hash in GLPI's database at the moment of login; nothing is cached.
- A GLPI login can never silently take over a same-named **local** account here
  (including the seeded `admin` fallback) — that's treated as a hard conflict and
  refused, not merged, so a coincidental username collision can't grant or lock out
  access.
- GLPI accounts don't get a local password reset option in this app (Admin ▸ User
  Management) — their password is managed in GLPI. Local accounts are unaffected
  and keep working as a fallback if GLPI is unreachable.

---

## 🔐 Security Notes

- Passwords: PBKDF2-HMAC-SHA256, salted, 260k iterations. Legacy unsalted SHA-256
  hashes (from earlier versions of this app) are upgraded transparently on next
  login.
- Sessions: random 256-bit tokens, stored **hashed**, expiring after 12 hours.
  Logout, password change, and user deletion all revoke sessions.
- Server-side export paths (Save PDF to local path, Read-Only bulk export) are
  constrained by an admin-configured allowlist — **Admin ▸ Export Paths**. Empty
  allowlist means unrestricted, which is the default; set at least one root before
  relying on this in a shared environment.

---

## 🧪 Tests

Self-contained scripts against throwaway databases/temp folders — no live GLPI or
server required:

```bash
python tests/test_auth.py          # password hashing, sessions, legacy upgrade
python tests/test_paths.py         # export-path allowlist / traversal guards
python tests/test_preview.py       # lazy PDF page rendering
python tests/test_sop.py           # department -> folder resolution
python tests/test_glpi_roster.py   # roster provisioning, local-account conflict guard
python tests/test_glpi_client.py   # GLPI DB client (mocked connection, real bcrypt)
```

---

## 📂 Project Structure

```
 ├─ index.py         # Streamlit application (UI + tool tabs)
 ├─ database.py      # SQLite: users, sessions, permissions, settings, roster
 ├─ glpi.py          # GLPI database client (read-only, no REST API)
 ├─ sop.py           # Department -> SOP folder resolution
 ├─ pathsafe.py      # Server-side export path allowlist
 ├─ tests/           # Test suites (see above)
 ├─ requirements.txt
 ├─ run_dev.bat
 └─ run_prod.bat
```

---

## 🛡 Known Limitations

- The preview renders up to the first 100 pages of a document; stamps still apply
  to every page in their configured range regardless of the preview cap.
- Session tokens travel in the URL query string (`?session=...`) — Streamlit has no
  native support for HttpOnly cookies. Tokens are short-lived and hashed at rest,
  but this is still a real exposure if that matters for your compliance posture.
- Department resolution from GLPI takes one entity per user (see the GLPI section
  above) — verify this fits before a full rollout.
