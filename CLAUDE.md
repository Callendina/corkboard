# Corkboard

Shared forum service for bug reports, feature requests, and Q&A. Serves multiple frontend apps from a single instance, sitting behind Caddy and Gatekeeper.

## Architecture

Corkboard is a FastAPI service that runs alongside Gatekeeper on the same server. It does NOT do its own authentication — it trusts `X-Gatekeeper-*` headers injected by Caddy's `forward_auth`.

### Request flow

```
Browser → Caddy (HTTPS) → forward_auth → Gatekeeper → 200 + headers → Caddy → Corkboard (localhost:9200)
```

Caddy routes `/corkboard/*` paths to corkboard, with the same `forward_auth` block used for the parent app. Gatekeeper resolves the user's identity and role for that app's domain, then corkboard receives the headers.

### Caddy config (per app)

```caddyfile
myapp.example.com {
    handle /_auth/* {
        reverse_proxy localhost:9100          # gatekeeper
    }
    handle /corkboard/* {
        forward_auth localhost:9100 {
            uri /_auth/verify
            copy_headers X-Gatekeeper-User X-Gatekeeper-Role X-Gatekeeper-System-Admin
        }
        reverse_proxy localhost:9200          # corkboard
    }
    handle {
        forward_auth localhost:9100 { ... }
        reverse_proxy localhost:APP_PORT      # the app
    }
}
```

### Multi-tenancy

Corkboard identifies which app a request is for from the `Host` header, same pattern as gatekeeper. Each app has its own config fragment in `config.d/`, its own post numbering, and its own data namespace (via `app_slug`).

## Auth model

Corkboard reads three headers on every request:

| Header | Meaning |
|--------|---------|
| `X-Gatekeeper-User` | Email of signed-in user (empty = anonymous) |
| `X-Gatekeeper-Role` | Role for this app: `user`, `admin`, or empty |
| `X-Gatekeeper-System-Admin` | `"true"` if gatekeeper superuser |

Three effective roles in corkboard:
- **anon**: no email, read-only (configurable per app)
- **user**: signed in, can post/comment/vote
- **admin**: can moderate (delete posts/comments), sees full emails

Roles are inherited from the parent app via gatekeeper. A vispay admin is automatically a corkboard admin when viewing vispay's corkboard.

## Board types

### General (Q&A / Discussion)
Free-form discussion threads. No lifecycle states. Categories are configurable per app.

### Structured (Bug Reports / Feature Requests)
Posts have a lifecycle status: `open` → `acknowledged` → `in_progress` → `done` / `wont_fix` / `duplicate`. Status changes are managed via the developer API and generate system comments visible to users.

## Developer API

JSON API at `/corkboard/api/dev/...` for lifecycle management. Authenticated via `X-Corkboard-Dev-Key` header (per-app secret, set in config). Not exposed to end users.

Key endpoints:
- `GET /api/dev/items` — list/filter structured posts
- `GET /api/dev/items/export` — markdown or JSON export (for feeding into todo tools)
- `PATCH /api/dev/items/{number}` — update status, add dev note
- `PATCH /api/dev/items/bulk` — bulk status update
- `POST /api/dev/items/{number}/comment` — add developer comment
- `POST /api/dev/items/{number}/tags` — set tags

Status changes via the dev API auto-generate system comments on the forum post.

## Data scrubbing

### Write-time scrubbing (scrub.py)
**MUST** be called on all user-supplied text BEFORE any storage or logging. Destructively removes:
- Credit/debit card numbers (Luhn-validated)
- NZ bank account numbers
- Bearer tokens
- Long hex/base64 strings (likely API keys)

The `scrubbed` boolean on posts/comments flags content that had data removed.

### Read-time display masking (scrub.py)
Author emails are displayed differently per viewer:
- **admin**: full email
- **user**: `j***@e***.com`
- **anon**: `User #N`

This happens at render time. Full emails are stored in the DB (needed for dev API, deduplication).

## Tech stack

- **Python 3.11+** with **FastAPI** and **uvicorn**
- **SQLite** via SQLAlchemy async (aiosqlite)
- **Jinja2** templates for forum UI
- **Markdown** library for rendering post/comment bodies (server-side, at write time)
- **ProxyHeadersMiddleware** to trust X-Forwarded-* from Caddy

## Project structure

```
corkboard/
  app.py              - FastAPI app setup, lifespan, routers
  config.py           - YAML config loading (main + config.d/ fragments)
  database.py         - SQLAlchemy async engine/session setup
  models.py           - All database models
  scrub.py            - Write-time PII scrubbing + read-time email masking
  auth.py             - Read X-Gatekeeper-* headers, RequestUser dependency
  routes/
    board.py          - Forum UI routes (list, view, create, comment, vote)
    admin.py          - Admin moderation routes
    dev_api.py        - Developer API (lifecycle, export, tagging)
  templates/          - Jinja2 HTML templates
  static/             - CSS, JS
config.d/             - Per-app config fragments (gitignored)
config.d.example/     - Example app config
config.example.yaml   - Main config example
run.py                - Entry point
```

## Running

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml  # edit with your values
mkdir config.d
# copy and edit config.d.example/myapp.yaml → config.d/yourapp.yaml
python run.py
```

## Config

### Main config (config.yaml)

```yaml
server:
  host: "127.0.0.1"
  port: 9200
  secret_key: "..."
  database_url: "sqlite+aiosqlite:///corkboard.db"
  mount_prefix: "/corkboard"
  upload_dir: "./uploads"
```

### Per-app config (config.d/appname.yaml)

```yaml
app_slug: "myapp"
domains: ["myapp.example.com"]
app_name: "My App"
dev_api_key: "secret"
theme_file: "/path/to/corkboard-theme.json"
categories:
  general: ["General", "How do I...?"]
  structured: ["Bug Report", "Feature Request"]
webhooks: []
rate_limits:
  posts_per_hour: 5
  comments_per_hour: 20
anonymous_access: "read_only"  # "read_only" | "none" | "post_allowed"
```

## Theming

Each frontend app provides a `corkboard-theme.json` file with CSS variables and branding. Corkboard's templates use CSS custom properties (`--cb-bg`, `--cb-accent`, etc.) that can be overridden by the theme file. Theme loading is not yet implemented — the default dark theme is used.

## Key decisions

- **No cookies or sessions** — corkboard has no auth state of its own. Everything comes from gatekeeper headers.
- **Per-app post numbering** — users reference `#1`, `#2`, etc. within their app's corkboard. Allocated atomically from `app_counters` table.
- **Server-side markdown** — rendered at write time, stored as HTML. Prevents XSS and means API exports have both formats.
- **Soft deletes everywhere** — `deleted_at` timestamp, never hard delete. Admins can see deleted content.
- **Scrubbing before storage** — sensitive data is stripped before it reaches the database or any log. The `scrub_sensitive()` function must be the first thing that touches user text.
- **Separate database** — corkboard has its own SQLite DB, not shared with gatekeeper. References users by email string, not by gatekeeper user ID.
- **Mount prefix** — all routes under `/corkboard` (configurable). Coexists with the parent app on the same domain.
