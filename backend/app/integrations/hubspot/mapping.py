"""Field mapping rules for the HubSpot integration (plan §5.1, §6).

Ownership: HubSpot owns every standard property; Outmate owns only the
``outmate_*`` namespace — the four properties created manually in H0
step 6. :func:`ensure_push_property` is the overwrite guard: anything
outside :data:`ALLOWED_PUSH_PROPERTIES` is rejected with
``import_conflict`` BEFORE any PATCH is attempted.

``slugify_column`` + ``suggest_property`` turn a column name into the
``outmate_<slug>`` default the push dialog prefills; the suggestion is
advisory — the allowlist above still decides what may be written.

``serialize_value`` renders cell values for HubSpot's custom text
properties, which take strings.
"""

import re

from app.errors.integration_error import IntegrationError

ALLOWED_PUSH_PROPERTIES: frozenset[str] = frozenset(
    {
        "outmate_tech_stack",
        "outmate_description",
        "outmate_growth_signals",
        "outmate_leadership",
    }
)
"""The only writable company properties in v1 — the ``outmate_*``
properties created by hand in the sandbox (H0 step 6). Everything else
is HubSpot-owned and must never be PATCHed."""

_SLUG_SPLIT = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 40


def slugify_column(name: str) -> str:
    """Lowercase slug for a column name: every ``[^a-z0-9]`` run becomes
    ``_``, edges trimmed, capped at 40 chars."""
    return _SLUG_SPLIT.sub("_", name.lower()).strip("_")[:_SLUG_MAX]


def suggest_property(column_name: str) -> str:
    """The ``outmate_<slug>`` property suggested for a column — a UI
    default only; the allowlist is enforced at push time."""
    return "outmate_" + slugify_column(column_name)


def serialize_value(value: str | int | float | bool) -> str:
    """Render a cell value as the string HubSpot custom text properties
    take; booleans become ``"true"``/``"false"``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def ensure_push_property(property_name: str) -> str:
    """The overwrite guard: return ``property_name`` iff it is in
    :data:`ALLOWED_PUSH_PROPERTIES`, else raise ``import_conflict``.

    Called immediately before any PATCH so a standard (sales-owned)
    property can never be written even if a caller skips validation.
    """
    if property_name not in ALLOWED_PUSH_PROPERTIES:
        raise IntegrationError(
            code="import_conflict",
            message=(
                f"{property_name!r} is not a writable integration property; "
                "only the outmate_* properties may be pushed"
            ),
        )
    return property_name
