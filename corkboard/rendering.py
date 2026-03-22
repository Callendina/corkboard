"""Markdown rendering with HTML sanitisation via bleach."""
import bleach
import markdown

# Allowed HTML tags after markdown rendering
ALLOWED_TAGS = [
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "dl", "dt",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "ins",
    "li", "ol", "p", "pre", "s", "strong", "sub", "sup", "table", "tbody",
    "td", "th", "thead", "tr", "ul",
]

ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "img": ["src", "alt", "title"],
    "td": ["align"],
    "th": ["align"],
}


def render_markdown(text: str) -> str:
    """Render markdown to sanitised HTML."""
    raw_html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "nl2br"],
    )
    return bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        strip=True,
    )
