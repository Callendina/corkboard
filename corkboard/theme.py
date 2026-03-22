"""Load per-app theme from JSON file."""
import json
import logging

logger = logging.getLogger("corkboard.theme")

# Cache: {theme_file_path: css_string}
_theme_cache: dict[str, str] = {}

DEFAULT_THEME = {
    "--cb-bg": "#1a1a2e",
    "--cb-surface": "#16213e",
    "--cb-text": "#e0e0e0",
    "--cb-accent": "#32aadd",
    "--cb-border": "#1f2937",
    "--cb-font": "system-ui, sans-serif",
    "--cb-muted": "#64748b",
    "--cb-danger": "#ef4444",
    "--cb-success": "#22c55e",
}


def load_theme(theme_file: str) -> dict:
    """Load theme JSON file. Returns dict with css_variables, logo_url, etc."""
    if not theme_file:
        return {"css_variables": DEFAULT_THEME}

    if theme_file in _theme_cache:
        return _theme_cache[theme_file]

    try:
        with open(theme_file) as f:
            data = json.load(f)
        _theme_cache[theme_file] = data
        logger.info(f"Loaded theme from {theme_file}")
        return data
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to load theme from {theme_file}: {e}")
        return {"css_variables": DEFAULT_THEME}


def theme_css_override(theme_file: str) -> str:
    """Return a CSS :root block with variable overrides from the theme file."""
    data = load_theme(theme_file)
    css_vars = data.get("css_variables", {})
    if not css_vars:
        return ""

    lines = [":root {"]
    for key, value in css_vars.items():
        # Ensure keys start with --
        if not key.startswith("--"):
            key = f"--{key}"
        lines.append(f"  {key}: {value};")
    lines.append("}")
    return "\n".join(lines)


def theme_meta(theme_file: str) -> dict:
    """Return non-CSS theme metadata (logo, back_url, etc.)."""
    data = load_theme(theme_file)
    return {
        "logo_url": data.get("logo_url", ""),
        "favicon_url": data.get("favicon_url", ""),
        "app_name": data.get("app_name", ""),
        "back_url": data.get("back_url", "/"),
        "back_label": data.get("back_label", "Back"),
    }


def clear_cache():
    """Clear the theme cache (call on config reload)."""
    _theme_cache.clear()
