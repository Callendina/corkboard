"""In-memory write rate limiting for posts and comments."""
import time

# {key: [timestamp, ...]}
_post_log: dict[str, list[float]] = {}
_comment_log: dict[str, list[float]] = {}

WINDOW = 3600.0  # 1 hour


def _check(log: dict[str, list[float]], key: str, limit: int) -> bool:
    """Returns True if the action is allowed, False if rate limited."""
    if limit <= 0:
        return True

    now = time.time()
    cutoff = now - WINDOW

    entries = log.get(key, [])
    entries = [t for t in entries if t > cutoff]
    log[key] = entries

    if len(entries) >= limit:
        return False

    entries.append(now)
    return True


def check_post_rate(identifier: str, limit: int) -> bool:
    """Check if a user/IP can create a post. identifier is email or IP."""
    return _check(_post_log, identifier, limit)


def check_comment_rate(identifier: str, limit: int) -> bool:
    """Check if a user/IP can create a comment."""
    return _check(_comment_log, identifier, limit)


def cleanup_old_entries():
    """Remove expired entries. Call periodically."""
    now = time.time()
    cutoff = now - WINDOW
    for log in (_post_log, _comment_log):
        for key in list(log.keys()):
            log[key] = [t for t in log[key] if t > cutoff]
            if not log[key]:
                del log[key]
