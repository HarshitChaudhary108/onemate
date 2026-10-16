"""Input domain normalization.

Accepts a bare hostname or an ``http(s)://`` URL and produces a normalized
hostname plus a ``www.``-stripped comparison key. Purely syntactic: this module
never performs DNS lookups or fetches anything.

Rules (binding: spec section 8 / tech section 4.2):

- The raw input is capped at 2048 characters to bound parsing work.
- ``http(s)://`` URLs: path, query and fragment are dropped; embedded
  credentials and an explicit port make the input invalid.
- Bare input must be a hostname only (no path, query, fragment, credentials,
  or port).
- The host is lowercased, one trailing dot is stripped, and IDNs are converted
  to punycode via the stdlib ``encodings.idna`` codec.
- ``compare_key`` additionally removes one leading ``www.`` label.
- Rejected: ``localhost``, any IP literal (a company domain must be a
  hostname, public or not), single-label hosts, labels longer than 63 chars,
  labels with characters outside ``a-z 0-9 -``, labels starting or ending with
  a hyphen, hosts longer than 253 chars, control characters, and empty input.
"""

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

_MAX_RAW_LENGTH = 2048
_MAX_HOST_LENGTH = 253
_MAX_LABEL_LENGTH = 63
_LABEL_RE = re.compile(r"^[a-z0-9-]+$")


class DomainError(ValueError):
    """Raised when input cannot be normalized into a valid company domain."""


@dataclass(frozen=True)
class NormalizedDomain:
    """Result of domain normalization.

    ``original`` is the trimmed string the user submitted; ``host`` is the
    normalized hostname (keeps ``www.``); ``compare_key`` is ``host`` with one
    leading ``www.`` removed, for identity/cache comparisons.
    """

    original: str
    host: str
    compare_key: str


def _has_control_chars(text: str) -> bool:
    return any(unicodedata.category(ch) == "Cc" for ch in text)


def _extract_host(candidate: str) -> str:
    """Return the raw host portion of ``candidate`` or raise ``DomainError``."""
    if "://" in candidate:
        try:
            parts = urlsplit(candidate)
        except ValueError as exc:
            raise DomainError("malformed URL") from exc
        if parts.scheme.lower() not in ("http", "https"):
            raise DomainError("only http:// and https:// URLs are accepted")
        if parts.username is not None or parts.password is not None:
            raise DomainError("embedded credentials are not allowed")
        try:
            host = parts.hostname
            port = parts.port
        except ValueError as exc:
            raise DomainError("malformed host or port") from exc
        if port is not None:
            raise DomainError("an explicit port is not allowed")
        if not host:
            raise DomainError("missing host")
        return host

    # Bare input must be a bare hostname: no path, query, fragment,
    # credentials (@), or port/IPv6 colons.
    if any(ch in candidate for ch in "/?#@:"):
        raise DomainError("expected a bare hostname or an http(s):// URL")
    return candidate


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _validate_host(host: str) -> None:
    """Validate a punycode/lowercased hostname. Raises ``DomainError``."""
    if host == "localhost":
        raise DomainError("localhost is not a valid company domain")
    if _is_ip_literal(host):
        raise DomainError("IP literals are not valid company domains")
    if len(host) > _MAX_HOST_LENGTH:
        raise DomainError("hostname is longer than 253 characters")

    labels = host.split(".")
    if len(labels) < 2:
        raise DomainError("a company domain must have at least two labels")
    for label in labels:
        if len(label) > _MAX_LABEL_LENGTH:
            raise DomainError("a hostname label is longer than 63 characters")
        if not _LABEL_RE.match(label):
            raise DomainError("a hostname label contains invalid characters")
        if label.startswith("-") or label.endswith("-"):
            raise DomainError("a hostname label cannot start or end with a hyphen")


def normalize_domain(raw: str) -> NormalizedDomain:
    """Normalize a user-supplied domain or http(s) URL.

    Raises:
        DomainError: if the input is empty, malformed, or not an acceptable
            company hostname.
    """
    if len(raw) > _MAX_RAW_LENGTH:
        raise DomainError("input is longer than 2048 characters")
    original = raw.strip()
    if not original:
        raise DomainError("empty input")
    if _has_control_chars(original):
        raise DomainError("control characters are not allowed")

    host = _extract_host(original).lower()
    if host.endswith("."):
        host = host[:-1]
    if not host:
        raise DomainError("missing host")

    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DomainError("internationalized domain cannot be converted to punycode") from exc

    _validate_host(host)

    return NormalizedDomain(
        original=original,
        host=host,
        compare_key=host.removeprefix("www."),
    )
