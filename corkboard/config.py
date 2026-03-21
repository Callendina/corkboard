"""YAML config loading with config.d/ fragment support."""
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class CategoriesConfig:
    general: list[str] = field(default_factory=lambda: ["General"])
    structured: list[str] = field(default_factory=lambda: ["Bug Report", "Feature Request"])


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
    categories: CategoriesConfig = field(default_factory=CategoriesConfig)
    webhooks: list[WebhookConfig] = field(default_factory=list)
    rate_limits: RateLimitsConfig = field(default_factory=RateLimitsConfig)
    anonymous_access: str = "read_only"  # "read_only" | "none" | "post_allowed"


@dataclass
class CorkboardConfig:
    host: str = "127.0.0.1"
    port: int = 9200
    secret_key: str = ""
    database_url: str = "sqlite+aiosqlite:///corkboard.db"
    mount_prefix: str = "/corkboard"
    upload_dir: str = "./uploads"
    apps: dict[str, AppConfig] = field(default_factory=dict)

    def app_for_domain(self, domain: str) -> AppConfig | None:
        for app in self.apps.values():
            if domain in app.domains:
                return app
        return None


def _parse_categories(raw: dict | None) -> CategoriesConfig:
    if not raw:
        return CategoriesConfig()
    return CategoriesConfig(
        general=raw.get("general", ["General"]),
        structured=raw.get("structured", ["Bug Report", "Feature Request"]),
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

    return AppConfig(
        slug=slug,
        domains=raw.get("domains", []),
        app_name=raw.get("app_name", raw.get("name", slug)),
        dev_api_key=raw.get("dev_api_key", ""),
        theme_file=raw.get("theme_file", ""),
        categories=_parse_categories(raw.get("categories")),
        webhooks=webhooks,
        rate_limits=RateLimitsConfig(
            posts_per_hour=rl.get("posts_per_hour", 5),
            comments_per_hour=rl.get("comments_per_hour", 20),
        ),
        anonymous_access=raw.get("anonymous_access", "read_only"),
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
        upload_dir=server.get("upload_dir", "./uploads"),
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
