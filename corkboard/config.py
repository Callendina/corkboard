"""YAML config loading with config.d/ fragment support."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


# Hardcoded post type definitions — fields beyond title/body
POST_TYPE_FIELDS = {
    "bug": {
        "steps_to_reproduce": {"label": "Steps to Reproduce", "type": "textarea", "required": True},
        "expected_behaviour": {"label": "Expected Behaviour", "type": "textarea", "required": True},
        "actual_behaviour": {"label": "Actual Behaviour", "type": "textarea", "required": True},
        "severity": {"label": "Severity", "type": "select", "options": ["low", "medium", "high", "critical"], "required": True},
    },
    "feature": {
        "use_case": {"label": "Use Case", "type": "textarea", "required": True},
        "priority": {"label": "Priority", "type": "select", "options": ["nice_to_have", "important", "essential"], "required": True},
    },
    "todo": {
        "priority": {"label": "Priority", "type": "select", "options": ["high", "medium", "low", "long_term_goal", "probably_never"], "required": True},
        "assigned_to": {"label": "Assigned To", "type": "text", "required": False},
        "due_date": {"label": "Due Date", "type": "date", "required": False},
    },
    "general": {},
}

VALID_POST_TYPES = set(POST_TYPE_FIELDS.keys())

# Per-post-type lifecycle statuses
POST_TYPE_STATUSES = {
    "bug": {"open", "acknowledged", "in_progress", "done", "wont_fix", "duplicate"},
    "feature": {"open", "acknowledged", "in_progress", "done", "wont_fix", "duplicate", "parked"},
    "todo": {"under_review", "under_development", "testing", "done", "wont_do", "parked"},
    "general": set(),
}

# Initial status when a lifecycle post is created
POST_TYPE_INITIAL_STATUS = {
    "bug": "open",
    "feature": "open",
    "todo": "under_review",
    "general": None,
}


@dataclass
class ForumConfig:
    slug: str = ""
    name: str = ""
    description: str = ""
    forum_type: str = "general"  # "general" | "lifecycle"
    post_types: list[str] = field(default_factory=lambda: ["general"])
    read_roles: list[str] = field(default_factory=lambda: ["anon", "user", "admin"])
    post_roles: list[str] = field(default_factory=lambda: ["user", "admin"])
    comment_roles: list[str] = field(default_factory=lambda: ["user", "admin"])
    sort_order: int = 0


@dataclass
class WebhookConfig:
    url: str = ""
    events: list[str] = field(default_factory=list)


@dataclass
class RateLimitsConfig:
    posts_per_hour: int = 5
    comments_per_hour: int = 20


@dataclass
class AppConfig:
    slug: str = ""
    domains: list[str] = field(default_factory=list)
    app_name: str = ""
    dev_api_key: str = ""
    theme_file: str = ""
    forums: list[ForumConfig] = field(default_factory=list)
    webhooks: list[WebhookConfig] = field(default_factory=list)
    rate_limits: RateLimitsConfig = field(default_factory=RateLimitsConfig)

    def get_forum(self, forum_slug: str) -> ForumConfig | None:
        for f in self.forums:
            if f.slug == forum_slug:
                return f
        return None

    def forums_visible_to(self, role: str) -> list[ForumConfig]:
        return [f for f in self.forums if role in f.read_roles]


@dataclass
class CorkboardConfig:
    host: str = "127.0.0.1"
    port: int = 9200
    secret_key: str = ""
    database_url: str = "sqlite+aiosqlite:///corkboard.db"
    mount_prefix: str = "/corkboard"
    apps: dict[str, AppConfig] = field(default_factory=dict)

    def app_for_domain(self, domain: str) -> AppConfig | None:
        for app in self.apps.values():
            if domain in app.domains:
                return app
        return None


def _parse_forum(raw: dict, index: int) -> ForumConfig:
    post_roles = raw.get("post_roles", ["user", "admin"])
    return ForumConfig(
        slug=raw.get("slug", f"forum-{index}"),
        name=raw.get("name", raw.get("slug", f"Forum {index}")),
        description=raw.get("description", ""),
        forum_type=raw.get("type", "general"),
        post_types=raw.get("post_types", ["general"]),
        read_roles=raw.get("read_roles", ["anon", "user", "admin"]),
        post_roles=post_roles,
        comment_roles=raw.get("comment_roles", post_roles),
        sort_order=raw.get("sort_order", index),
    )


def _parse_app_config(slug: str, raw: dict) -> AppConfig:
    webhooks = []
    for wh in raw.get("webhooks", []) or []:
        if isinstance(wh, dict):
            webhooks.append(WebhookConfig(
                url=wh.get("url", ""),
                events=wh.get("events", []),
            ))

    rl = raw.get("rate_limits", {}) or {}

    forums = []
    for i, f_raw in enumerate(raw.get("forums", []) or []):
        forums.append(_parse_forum(f_raw, i))

    return AppConfig(
        slug=slug,
        domains=raw.get("domains", []),
        app_name=raw.get("app_name", raw.get("name", slug)),
        dev_api_key=raw.get("dev_api_key", ""),
        theme_file=raw.get("theme_file", ""),
        forums=forums,
        webhooks=webhooks,
        rate_limits=RateLimitsConfig(
            posts_per_hour=rl.get("posts_per_hour", 5),
            comments_per_hour=rl.get("comments_per_hour", 20),
        ),
    )


def load_config(path: str = "config.yaml") -> CorkboardConfig:
    data = {}
    if os.path.exists(path):
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    server = data.get("server", {}) or {}

    config = CorkboardConfig(
        host=server.get("host", "127.0.0.1"),
        port=server.get("port", 9200),
        secret_key=server.get("secret_key", ""),
        database_url=server.get("database_url", "sqlite+aiosqlite:///corkboard.db"),
        mount_prefix=server.get("mount_prefix", "/corkboard"),
    )

    # Load apps from main config
    for slug, app_raw in (data.get("apps") or {}).items():
        config.apps[slug] = _parse_app_config(slug, app_raw)

    # Load config.d/ fragments (override main config)
    config_d = Path("config.d")
    if config_d.is_dir():
        for frag in sorted(config_d.glob("*.yaml")):
            slug = frag.stem
            with open(frag) as f:
                raw = yaml.safe_load(f) or {}
            # Allow fragment to override slug
            slug = raw.pop("app_slug", slug)
            config.apps[slug] = _parse_app_config(slug, raw)

    return config
