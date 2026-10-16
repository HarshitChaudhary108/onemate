"""Safe URL handling for source links.

Only `http`/`https` URLs with a real host and no embedded credentials may
reach the UI. Anything else is dropped (returns ``None``); this function never
raises, because provider payloads are untrusted data.
"""

from urllib.parse import urlsplit

_MAX_URL_LENGTH = 2048


def safe_url(value: str | None) -> str | None:
    """Return ``value`` if it is a safe http/https URL, else ``None``.

    Unsafe means: empty, non-http(s) scheme (``javascript:``, ``data:``,
    ``file:``, ...), embedded credentials (``user:pass@host``), or a missing /
    whitespace-containing host. The input is capped at 2048 characters to
    bound parsing work on untrusted provider data.
    """
    if value is None:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > _MAX_URL_LENGTH:
        return None
    try:
        parts = urlsplit(candidate)
        if parts.scheme.lower() not in ("http", "https"):
            return None
        if parts.username is not None or parts.password is not None:
            return None
        host = parts.hostname
    except ValueError:
        return None
    if not host or any(ch.isspace() for ch in host):
        return None
    return candidate
