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

## Forums

Each app defines one or more **forums** in its config. Forums come in two types:

### General forums
Free-form discussion threads. Posts have type `general` with title + body only.

### Lifecycle forums
Posts have a structured type (`bug`, `feature`, or `todo`) with type-specific fields and a lifecycle status: `open` → `acknowledged` → `in_progress` → `done` / `wont_fix` / `duplicate`.

### Post types and their fields
- **bug**: steps_to_reproduce, expected_behaviour, actual_behaviour, severity (low/medium/high/critical)
- **feature**: use_case, priority (nice_to_have/important/essential)
- **todo**: priority (low/medium/high/critical), assigned_to, due_date
- **general**: title + body only

Post type field definitions are hardcoded in `config.py:POST_TYPE_FIELDS`. Fields are stored as JSON in the `fields_json` column.

### Per-forum access control
Each forum has `read_roles` and `post_roles` lists. Roles are `anon`, `user`, `admin` — inherited from gatekeeper headers. A forum can be read-only for anon users but writable by signed-in users.

### Moving posts between forums
Posts can be moved via the dev API (`POST /api/dev/items/{number}/move`). The original forum is recorded in `moved_from_forum`. A system comment is added. If moved to a lifecycle forum, status is set to `open`.

### Related posts
Posts can link to an originating post via the `related_to` field (post number). Typically used when a todo is created from a bug report or feature request. Set via the dev API `POST /api/dev/items/create` with `"related_to": N`. Displayed as a link in the post detail meta.

### Forum config example
```yaml
forums:
  - slug: "bugs"
    name: "Bug Reports"
    description: "Found something broken? Report it here."
    type: "lifecycle"
    post_types: ["bug"]
    read_roles: ["anon", "user", "admin"]
    post_roles: ["user", "admin"]
  - slug: "roadmap"
    name: "Development Roadmap"
    type: "lifecycle"
    post_types: ["todo", "bug", "feature"]
    read_roles: ["anon", "user", "admin"]
    post_roles: ["admin"]
  - slug: "general"
    name: "Discussion"
    type: "general"
    post_types: ["general"]
    read_roles: ["user", "admin"]
    post_roles: ["user", "admin"]
```

## URL structure

```
/corkboard/                          → forum index (list of forums)
/corkboard/f/{forum_slug}            → posts in a forum
/corkboard/f/{forum_slug}/new        → new post (type picker if multiple types)
/corkboard/post/{number}             → single post + comments
/corkboard/admin/                    → admin moderation
/corkboard/api/dev/...               → developer API
```

## Developer API

JSON API at `/corkboard/api/dev/...` for lifecycle management. Not exposed to end users.

### Authentication (checked in order)
1. **Gatekeeper headers** — if `X-Gatekeeper-Role: admin` or `X-Gatekeeper-System-Admin: true` (set by Caddy forward_auth for browser-based admin users)
2. **X-API-Key header** — matched against the app's `dev_api_key` from its YAML config (for programmatic/CLI access)

Key endpoints:
- `GET /api/dev/forums` — list all forums for the app
- `GET /api/dev/items` — list/filter lifecycle posts (across all lifecycle forums or filtered by `?forum=`)
- `GET /api/dev/items/export` — markdown or JSON export (grouped by post type)
- `GET /api/dev/items/{number}` — single item with comments and structured fields
- `PATCH /api/dev/items/{number}` — update status, add dev note
- `PATCH /api/dev/items/bulk` — bulk status update
- `POST /api/dev/items/create` — create a post via API (typically a todo item)
- `POST /api/dev/items/{number}/move` — move post to a different forum
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
- **Markdown** + **bleach** for rendering post/comment bodies (server-side, at write time, HTML sanitised)
- **ProxyHeadersMiddleware** to trust X-Forwarded-* from Caddy

## Project structure

```
corkboard/
  app.py              - FastAPI app setup, lifespan, routers
  config.py           - YAML config loading (main + config.d/ fragments)
  database.py         - SQLAlchemy async engine/session setup
  models.py           - All database models
  rendering.py        - Markdown rendering with bleach HTML sanitisation
  theme.py            - Per-app theme loading from JSON files
  rate_limit.py       - In-memory write rate limiting for posts/comments
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

## Headless mode

Append `?layout=headless` to any page URL to get a stripped-down version with no header, transparent background, and minimal chrome. Designed for iframe embedding — the parent app provides its own navbar.

All internal navigation (links, form submits, redirects) automatically preserves the `layout=headless` param via client-side JS and server-side redirect helpers.

## Theming

Each frontend app provides a `corkboard-theme.json` file with CSS variables and branding. Corkboard's templates use CSS custom properties (`--cb-bg`, `--cb-accent`, etc.) that are overridden by the theme file.

Theme features:
- `css_variables` / `css_variables_dark` — light/dark mode CSS variable overrides
- `extra_css` — additional CSS (e.g. font imports). **Must be first** in the generated `<style>` block so `@import` rules work.
- `header_html_file` — custom header HTML replacing the default topbar
- `head_js` — JS snippet injected in `<head>` (e.g. dark mode detection). Runs in both normal and headless modes.
- `logo_url`, `favicon_url`, `back_url`, `back_label` — branding overrides

## Key decisions

- **No cookies or sessions** — corkboard has no auth state of its own. Everything comes from gatekeeper headers.
- **Per-app post numbering** — users reference `#1`, `#2`, etc. within their app's corkboard. Allocated atomically from `app_counters` table.
- **Server-side markdown** — rendered at write time, stored as HTML. Prevents XSS and means API exports have both formats.
- **Soft deletes everywhere** — `deleted_at` timestamp, never hard delete. Admins can see deleted content.
- **Scrubbing before storage** — sensitive data is stripped before it reaches the database or any log. The `scrub_sensitive()` function must be the first thing that touches user text.
- **Separate database** — corkboard has its own SQLite DB, not shared with gatekeeper. References users by email string, not by gatekeeper user ID.
- **Mount prefix** — all routes under `/corkboard` (configurable). Coexists with the parent app on the same domain.
