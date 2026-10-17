"""The D7 scope gate — a standalone, testable function.

D7 keeps the README's boundary: no phone numbers, no personal email
addresses, no contact inference (identifying who to contact at a company
or their details), and no buying-intent/lead scores. The planner's system
prompt instructs the model to answer such requests with
``intent: "unsupported"``; this module is the deterministic server-side
backstop — the planner's output is never trusted, so the gate runs on the
request text and on every planned column's name and prompt template.

Matching is deliberately targeted at *people/contact* data: public
company-level facts stay in scope (mentioning a "contact page" is fine),
while anything seeking an individual's contact details is refused.
"""

import re

# Each entry: (pattern, user-safe refusal reason). Reasons are stable
# enough to surface verbatim to the user and to the planner on repair.
_REFUSALS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bphones?\b|\btelephones?\b|\b(?:mobile|cell)[ -]?(?:number|phone)s?\b"
            r"|\bdirect[ -]?dials?\b|\blandlines?\b|\bphone[ -]?books?\b",
            re.IGNORECASE,
        ),
        "phone numbers are outside the supported scope",
    ),
    (
        re.compile(
            r"\b(?:personal|private|work|business|direct|contact)[ -]?e-?mails?\b"
            r"|\be-?mail addresses?\b|\be-?mails? (?:of|for|to|from)\b"
            r"|\b(?:ceo|cto|cfo|founder|owner|employee|staff|manager|"
            r"decision[ -]?maker)s?'?s? e-?mails?\b"
            r"|\b(?:find|guess|infer|hunt|scrape|look[ -]?up)\b[^.;]{0,40}\be-?mails?\b",
            re.IGNORECASE,
        ),
        "email addresses — especially personal ones — are outside the supported scope",
    ),
    (
        re.compile(
            r"\bcontact (?:info|information|details?|data|list)\b"
            r"|\bcontacts? (?:of|for|at)\b[^.;]{0,40}\b(?:compan|firm|account)\w*"
            r"|\bwho\b[^.;]{0,20}\bto contact\b"
            r"|\bbest (?:person|contact|way) to (?:contact|reach)\b"
            r"|\b(?:find|identify|guess|infer|look[ -]?up)\b[^.;]{0,40}\bcontacts?\b"
            r"|\bdecision[ -]?makers?[ -]?(?:e-?mail|phone|contact)\b",
            re.IGNORECASE,
        ),
        "contact inference — identifying who to contact or their details — "
        "is outside the supported scope",
    ),
    (
        re.compile(
            r"\blead scores?\b|\bbuying[ -]?intent\b|\bpurchase[ -]?intent\b"
            r"|\bintent scores?\b",
            re.IGNORECASE,
        ),
        "buying-intent and lead scores are outside the supported scope",
    ),
)


def scope_violation(text: str) -> str | None:
    """The refusal reason when ``text`` asks for out-of-scope data, else None.

    Applied to the raw request and to each planned column's name +
    template. Pure function — no I/O, no model call.
    """
    for pattern, reason in _REFUSALS:
        if pattern.search(text):
            return reason
    return None
