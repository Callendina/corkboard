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


def _css_block(selector: str, variables: dict) -> str:
    """Build a CSS block with variable declarations."""
    lines = [f"{selector} {{"]
    for key, value in variables.items():
        if not key.startswith("--"):
            key = f"--{key}"
        lines.append(f"  {key}: {value};")
    lines.append("}")
    return "\n".join(lines)


def theme_css_override(theme_file: str) -> str:
    """Return CSS with :root variables, optional dark mode overrides, and extra_css."""
    data = load_theme(theme_file)
    css_vars = data.get("css_variables", {})
    if not css_vars:
        return ""

    parts = [_css_block(":root", css_vars)]

    dark_vars = data.get("css_variables_dark", {})
    if dark_vars:
        parts.append(_css_block('[data-theme="dark"]', dark_vars))

    extra_css = data.get("extra_css", "")
    if extra_css:
        parts.append(extra_css)

    return "\n".join(parts)


def theme_meta(theme_file: str) -> dict:
    """Return non-CSS theme metadata (logo, back_url, etc.)."""
    data = load_theme(theme_file)
    return {
        "logo_url": data.get("logo_url", ""),
        "favicon_url": data.get("favicon_url", ""),
        "app_name": data.get("app_name", ""),
        "back_url": data.get("back_url", "/"),
        "back_label": data.get("back_label", "Back"),
        "head_js": data.get("head_js", ""),
        "header_html_file": data.get("header_html_file", ""),
    }


# Cache for header HTML files: {path: raw_content}
_header_cache: dict[str, str] = {}


def load_header_html(header_html_file: str, prefix: str, user_display: str,
                     user_role: str, forum_nav_html: str, app_name: str) -> str:
    """Load and render a custom header HTML fragment with placeholder replacement."""
    if not header_html_file:
        return ""

    if header_html_file not in _header_cache:
        try:
            with open(header_html_file) as f:
                _header_cache[header_html_file] = f.read()
            logger.info(f"Loaded header HTML from {header_html_file}")
        except FileNotFoundError:
            logger.warning(f"Header HTML file not found: {header_html_file}")
            return ""

    html = _header_cache[header_html_file]
    html = html.replace("{{CORKBOARD_PREFIX}}", prefix)
    html = html.replace("{{CORKBOARD_USER}}", user_display)
    html = html.replace("{{CORKBOARD_USER_ROLE}}", user_role)
    html = html.replace("{{CORKBOARD_FORUM_NAV}}", forum_nav_html)
    html = html.replace("{{CORKBOARD_APP_NAME}}", app_name)
    return html


def clear_cache():
    """Clear the theme cache (call on config reload)."""
    _theme_cache.clear()
    _header_cache.clear()
