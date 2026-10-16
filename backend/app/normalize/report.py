"""Deterministic report field normalization (spec section 7, tech section 5.5).

No LLM and no generated filler: every populated value is either quoted /
lightly trimmed provider text or a provider-returned field copied verbatim.
All provider strings are DATA — control characters are stripped and lengths
capped (summary 240, highlight 500, title 200, person/field names 120,
extracted field values 200). Unsafe URLs are dropped via ``safe_url`` (the
``Source`` model re-validates anyway).

Sources are attached per field/item and are NEVER copied report-wide. A
``verified`` value always carries at least one source with a real URL; values
that only come from MoltSets stay ``provider-reported`` because MoltSets
returns no per-field URLs.

Field rules (conservative — unparseable or missing evidence stays
``unavailable``; nothing is guessed):

- Description: first sentence of the first non-empty highlight on the first
  overview hit whose host matches the resolved domain, trimmed to 240 chars.
- Industry: an Exa ``"is a/an <phrase> company|platform|provider"`` phrase
  (``extract_industry``) from a domain-matching overview hit; otherwise the
  MoltSets ``industry`` field as provider-reported. If both exist and share
  no content token the field becomes ``unavailable`` plus a warning; if they
  share a token the Exa value wins (``verified``) and both sources are kept.
- Target market: only an explicit segment phrase from a domain-matching
  overview hit via ``extract_target_market``. Never inferred from the
  product category.
- Employee size: an Exa ``"<number> employees"`` statement from a
  domain-matching overview hit that HAS a ``published_date`` (``verified``);
  otherwise the exact MoltSets ``employee_count``/``employee_range`` string
  as returned (``provider-reported``). No precision is added.
- Growth signals: one item per growth-search hit that has a non-empty
  highlight containing >=1 keyword (case-insensitive whole word) AND a safe
  URL. Type = first matching group in the fixed order funding,
  leadership_change, hiring, expansion, product_launch; ``other`` is defined
  in the schema but intentionally unused (documented). Summary = the
  sentence containing the matched keyword, trimmed. Outer field is
  ``verified`` with >=1 item, else ``unavailable`` with the note
  "No supported signals found in this run."
- Decision-makers: name+role pairs extracted only when ONE sentence contains
  both a person name (2-4 capitalized tokens) and a role keyword
  (``extract_person_role``); merged with MoltSets people
  (``provider-reported``). Dedupe key = normalized name + resolved company
  compare_key + normalized role. Merged items keep the union of sources; an
  Exa-supported item may be ``verified``, a MoltSets-only value is never
  upgraded. ``mock`` is true if any contributing source is mock.
- Contact information: MoltSets-returned business email / risk grade /
  last-validated date only (``provider-reported``), MoltSets source with
  ``url=None``, fixed schema caveat text. Nothing is inferred.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlsplit

from app.providers.types import (
    ExaHit,
    ExaSearchResult,
    MoltSetsCompanyResult,
    MoltSetsPeopleResult,
)
from app.schemas.report import (
    ContactInfo,
    DecisionMaker,
    Field,
    GrowthSignal,
    GtmHypothesis,
    RationalePoint,
    Source,
)
from app.utils.safe_url import safe_url

# -- length caps (provider text is untrusted data) ---------------------------
MAX_SUMMARY_LEN = 240
MAX_HIGHLIGHT_LEN = 500
MAX_TITLE_LEN = 200
MAX_NAME_LEN = 120
MAX_VALUE_LEN = 200
MAX_EMAIL_LEN = 254

NO_GROWTH_NOTE = "No supported signals found in this run."
NO_HYPOTHESIS_NOTE = "Insufficient evidence to form a hypothesis."
INDUSTRY_CONFLICT_NOTE = "Exa and MoltSets industry values conflict."
HYPOTHESIS_CAVEAT = "Not confirmed buying intent."


# -- text utilities ----------------------------------------------------------


def clean_text(text: str | None, limit: int) -> str | None:
    """Strip control characters, trim, and cap ``text`` at ``limit`` chars.

    Returns ``None`` for missing/blank input. Provider text is data, never
    instructions — this only sanitizes, it never interprets.
    """
    if not text:
        return None
    cleaned = "".join(ch for ch in text if unicodedata.category(ch) != "Cc")
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def squash(text: str | None) -> str:
    """Casefold and keep alphanumerics only (name/host comparison helper)."""
    if not text:
        return ""
    return "".join(ch for ch in text.casefold() if ch.isalnum())


def first_sentence(text: str) -> str:
    """Return the first sentence of ``text`` (split on . ! ? followed by a
    space or end of string)."""
    match = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)
    return match[0].strip() if match else ""


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


# -- host helpers ------------------------------------------------------------


def hit_host_key(url: str | None) -> str | None:
    """Comparison key of a hit URL's host: same normalization as input
    domains — lowercase, trailing dot stripped, one ``www.`` label removed —
    but no validation (provider data). ``None`` when there is no usable host.
    """
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.lower().rstrip(".")
    return host.removeprefix("www.")


def host_matches(hit_key: str | None, domain_key: str) -> bool:
    """A hit matches the domain when its host key equals ``domain_key`` or is
    a subdomain of it."""
    return hit_key is not None and (hit_key == domain_key or hit_key.endswith(f".{domain_key}"))


def first_label_key_or_none(url: str | None) -> str | None:
    """The first label of a hit URL's host (e.g. ``example-robotics``)."""
    host_key = hit_host_key(url)
    if not host_key:
        return None
    return host_key.split(".", 1)[0]


# -- source builders ---------------------------------------------------------


def exa_source(
    hit_url: str | None,
    title: str | None,
    published_date: str | None,
    highlight: str | None,
    result: ExaSearchResult,
    clock: Callable[[], datetime],
) -> Source:
    """One Exa hit -> one Source. ``highlight`` is the specific excerpt that
    supports the claim when known (first highlight otherwise)."""
    return Source(
        provider="Exa",
        mock=result.mock,
        retrieved_at=clock(),
        url=hit_url,
        title=clean_text(title, MAX_TITLE_LEN),
        published_date=published_date,
        highlight=clean_text(highlight, MAX_HIGHLIGHT_LEN),
        query=result.query,
    )


def hit_source(
    hit: ExaHit,
    result: ExaSearchResult,
    clock: Callable[[], datetime],
    highlight: str | None = None,
) -> Source:
    """Convenience wrapper taking an ``ExaHit``."""
    if highlight is None:
        highlight = hit.highlights[0] if hit.highlights else None
    return exa_source(hit.url, hit.title, hit.published_date, highlight, result, clock)


def moltsets_source(
    result: MoltSetsCompanyResult | MoltSetsPeopleResult,
    clock: Callable[[], datetime],
) -> Source:
    """MoltSets has no per-field source URLs: provider + retrieval time only."""
    return Source(provider="MoltSets", mock=result.mock, retrieved_at=clock())


# -- extractors (documented, narrow, unit-tested) ----------------------------

# "Acme is a robotics platform" -> "robotics platform"; also matches
# "company" and "provider" as the closing noun.
_INDUSTRY_RE = re.compile(
    r"\bis\s+(?:a|an)\s+([A-Za-z][\w&-]*(?:\s+[A-Za-z][\w&-]*){0,4}?)"
    r"\s+(company|platform|provider)\b",
    re.IGNORECASE,
)

# Explicit segment phrases only — e.g. "built for logistics teams",
# "serves mid-size distribution centers", "for sales teams". Captures stop
# at clause punctuation so a following clause is never absorbed.
_TARGET_MARKET_RES = (
    re.compile(r"\bbuilt for\s+([^.,;:]{2,80})", re.IGNORECASE),
    re.compile(r"\bdesigned for\s+([^.,;:]{2,80})", re.IGNORECASE),
    re.compile(r"\bserves\s+([^.,;:]{2,80})", re.IGNORECASE),
    re.compile(r"\bsells to\s+([^.,;:]{2,80})", re.IGNORECASE),
    re.compile(r"\bfor\s+([^.,;:]{2,60}?\steams)\b", re.IGNORECASE),
)

_EMPLOYEE_RE = re.compile(r"\b(\d[\d,]*)\s+employees\b", re.IGNORECASE)

# Person name: 2-4 capitalized tokens (allows apostrophes/hyphens).
_PERSON_NAME_RE = re.compile(r"\b([A-Z][\w'’-]+(?:\s+[A-Z][\w'’-]+){1,3})\b")

# Role keywords, expanded to the full title where the wording allows it
# (e.g. "VP" -> "VP of Engineering", "Chief" -> "Chief Executive Officer").
_ROLE_RE = re.compile(
    r"\b(?:CEO|CTO|CFO|COO"
    r"|Co-founder|Founder|President"
    r"|Chief\s+[A-Z][\w-]+(?:\s+[A-Z][\w-]+)?\s+Officer"
    r"|Vice\s+President(?:\s+of\s+[A-Z][\w&-]+(?:\s+[A-Z][\w&-]+)*)?"
    r"|VP(?:\s+of\s+[A-Z][\w&-]+(?:\s+[A-Z][\w&-]+)*)?"
    r"|Head\s+of\s+[A-Z][\w&-]+(?:\s+[A-Z][\w&-]+)*"
    r"|Director)\b"
)


def extract_industry(text: str) -> str | None:
    """Extract ``"<phrase> company|platform|provider"`` after "is a/an"."""
    match = _INDUSTRY_RE.search(text)
    if not match:
        return None
    return clean_text(f"{match.group(1)} {match.group(2)}", MAX_VALUE_LEN)


def extract_target_market(text: str) -> str | None:
    """Extract an explicit segment phrase ("serves X", "built for X",
    "designed for X", "sells to X", "for X teams")."""
    for pattern in _TARGET_MARKET_RES:
        match = pattern.search(text)
        if match:
            return clean_text(match.group(1), MAX_VALUE_LEN)
    return None


def extract_employee_count(text: str) -> str | None:
    """Extract a literal ``"<number> employees"`` statement."""
    match = _EMPLOYEE_RE.search(text)
    return match.group(0) if match else None


def extract_person_role(sentence: str, company_squash: str) -> tuple[str, str] | None:
    """Return ``(name, role)`` when ONE sentence holds both a person name
    (2-4 capitalized tokens) and a role keyword.

    The name candidate nearest before the role match is preferred, then any
    candidate after it. Candidates that overlap the role text, equal/contain
    the resolved company name, or are themselves role words are skipped.
    """
    role_match = _ROLE_RE.search(sentence)
    if not role_match:
        return None
    candidates: list[tuple[int, str]] = []
    for match in _PERSON_NAME_RE.finditer(sentence):
        if match.start() < role_match.end() and match.end() > role_match.start():
            continue  # overlaps the role text
        name = match.group(1)
        name_squash = squash(name)
        if not name_squash:
            continue
        if company_squash and (name_squash in company_squash or company_squash in name_squash):
            continue  # the company itself is not a person
        if _ROLE_RE.search(name):
            continue  # e.g. "Vice President" tokens alone
        candidates.append((match.start(), name))
    if not candidates:
        return None
    before = [c for c in candidates if c[0] < role_match.start()]
    name = before[-1][1] if before else candidates[0][1]
    return name, role_match.group(0)


# -- growth keywords ----------------------------------------------------------

# Fixed order: the first group with a whole-word match decides the type.
# `other` exists in the schema but is intentionally never emitted.
_GROWTH_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("funding", ("raise", "raises", "raised", "funding", "series", "seed", "investment")),
    (
        "leadership_change",
        ("appoints", "appointed", "names", "new ceo", "steps down", "joins as"),
    ),
    ("hiring", ("hiring", "hires", "recruiting", "headcount")),
    ("expansion", ("expands", "expansion", "opens", "new office")),
    ("product_launch", ("launches", "launched", "unveils", "introduces", "releases")),
)


def _keyword_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


_GROWTH_RES = tuple((kind, _keyword_pattern(words)) for kind, words in _GROWTH_KEYWORDS)


# -- field builders -----------------------------------------------------------


def _default_clock() -> Callable[[], datetime]:
    return lambda: datetime.now(UTC)


@dataclass
class EvidenceBundle:
    """Everything normalization needs, already fetched by the orchestrator.

    ``domain_key`` is the resolved comparison key (``www.`` stripped).
    ``company_squash`` is the resolved company name in squashed form, used to
    keep the company itself out of person-name extraction.
    """

    domain_key: str
    company_squash: str
    overview: ExaSearchResult | None = None
    growth: ExaSearchResult | None = None
    leadership: ExaSearchResult | None = None
    moltsets_company: MoltSetsCompanyResult | None = None
    moltsets_people: MoltSetsPeopleResult | None = None
    clock: Callable[[], datetime] = field(default_factory=_default_clock)


def _domain_hits(result: ExaSearchResult | None, domain_key: str) -> list[ExaHit]:
    """Hits whose host matches the resolved domain (subdomains count)."""
    if result is None:
        return []
    return [h for h in result.hits if host_matches(hit_host_key(h.url), domain_key)]


def _unavailable_str(note: str | None = None) -> Field[str]:
    return Field[str](value=None, status="unavailable", sources=[], note=note)


def build_description(bundle: EvidenceBundle) -> Field[str]:
    """First sentence of the first non-empty highlight on the first
    domain-matching overview hit; lightly trimmed only."""
    result = bundle.overview
    if result is None:
        return _unavailable_str()
    for hit in _domain_hits(result, bundle.domain_key):
        for highlight in hit.highlights:
            sentence = clean_text(first_sentence(highlight), MAX_SUMMARY_LEN)
            if sentence:
                return Field[str](
                    value=sentence,
                    status="verified",
                    sources=[hit_source(hit, result, bundle.clock, highlight=highlight)],
                )
    return _unavailable_str()


def _token_set(text: str) -> set[str]:
    stop = {"company", "platform", "provider", "the", "and", "of", "a", "an", "ltd", "inc"}
    return {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if len(token) > 2 and token not in stop
    }


def build_industry(bundle: EvidenceBundle, warnings: list[str]) -> Field[str]:
    """Exa phrase (verified) preferred; MoltSets industry provider-reported;
    a token-less conflict becomes unavailable + warning."""
    exa_value: str | None = None
    exa_src: Source | None = None
    if bundle.overview is not None:
        for hit in _domain_hits(bundle.overview, bundle.domain_key):
            for highlight in hit.highlights:
                value = extract_industry(highlight)
                if value:
                    exa_value = value
                    exa_src = hit_source(hit, bundle.overview, bundle.clock, highlight=highlight)
                    break
            if exa_value:
                break

    ms_value: str | None = None
    ms_src: Source | None = None
    ms_company_result = bundle.moltsets_company
    company = ms_company_result.company if ms_company_result is not None else None
    if company is not None and ms_company_result is not None:
        ms_value = clean_text(company.industry, MAX_VALUE_LEN)
        if ms_value:
            ms_src = moltsets_source(ms_company_result, bundle.clock)

    both_sources = [s for s in (exa_src, ms_src) if s is not None]
    if exa_value and exa_src and ms_value:
        if _token_set(exa_value) & _token_set(ms_value):
            return Field[str](value=exa_value, status="verified", sources=both_sources)
        warnings.append(
            f"Industry conflict: Exa evidence says {exa_value!r}, "
            f"MoltSets reports {ms_value!r}; field left unavailable."
        )
        return Field[str](
            value=None,
            status="unavailable",
            sources=both_sources,
            note=INDUSTRY_CONFLICT_NOTE,
        )
    if exa_value and exa_src:
        return Field[str](value=exa_value, status="verified", sources=[exa_src])
    if ms_value and ms_src:
        return Field[str](value=ms_value, status="provider-reported", sources=[ms_src])
    return _unavailable_str()


def build_target_market(bundle: EvidenceBundle) -> Field[str]:
    """Explicit segment phrase from a domain-matching overview hit only."""
    result = bundle.overview
    if result is None:
        return _unavailable_str()
    for hit in _domain_hits(result, bundle.domain_key):
        for highlight in hit.highlights:
            value = extract_target_market(highlight)
            if value:
                return Field[str](
                    value=value,
                    status="verified",
                    sources=[hit_source(hit, result, bundle.clock, highlight=highlight)],
                )
    return _unavailable_str()


def build_employee_size(bundle: EvidenceBundle) -> Field[str]:
    """Dated Exa "<number> employees" statement (verified); otherwise the
    exact MoltSets count/range string as returned (provider-reported)."""
    result = bundle.overview
    if result is not None:
        for hit in _domain_hits(result, bundle.domain_key):
            if not hit.published_date:
                continue  # the public statement must be dated
            for highlight in hit.highlights:
                value = extract_employee_count(highlight)
                if value:
                    return Field[str](
                        value=clean_text(value, MAX_VALUE_LEN),
                        status="verified",
                        sources=[hit_source(hit, result, bundle.clock, highlight=highlight)],
                    )

    company = bundle.moltsets_company.company if bundle.moltsets_company else None
    if company is not None:
        value = clean_text(company.employee_count, MAX_VALUE_LEN) or clean_text(
            company.employee_range, MAX_VALUE_LEN
        )
        if value and bundle.moltsets_company is not None:
            return Field[str](
                value=value,
                status="provider-reported",
                sources=[moltsets_source(bundle.moltsets_company, bundle.clock)],
            )
    return _unavailable_str()


def build_growth_signals(bundle: EvidenceBundle) -> Field[list[GrowthSignal]]:
    """One item per growth-search hit whose highlight matches >=1 keyword.
    Hits without a keyword match or with an unsafe URL are excluded."""
    result = bundle.growth
    items: list[GrowthSignal] = []
    if result is not None:
        for hit in result.hits:
            if not hit.url or safe_url(hit.url) is None:
                continue  # cannot cite the item -> cannot verify it
            for highlight in hit.highlights:
                cleaned = clean_text(highlight, MAX_HIGHLIGHT_LEN)
                if not cleaned:
                    continue
                signal_type: str | None = None
                pattern: re.Pattern[str] | None = None
                for kind, compiled in _GROWTH_RES:
                    if compiled.search(cleaned):
                        signal_type, pattern = kind, compiled
                        break
                if signal_type is None or pattern is None:
                    continue  # no supported keyword -> hit is excluded
                summary = next(
                    (s for s in sentences(cleaned) if pattern.search(s)),
                    cleaned,
                )
                items.append(
                    GrowthSignal(
                        type=signal_type,
                        summary=clean_text(summary, MAX_SUMMARY_LEN) or "",
                        date=hit.published_date,
                        sources=[hit_source(hit, result, bundle.clock, highlight=highlight)],
                    )
                )
                break  # one item per hit
    if not items:
        return Field[list[GrowthSignal]](
            value=[], status="unavailable", sources=[], note=NO_GROWTH_NOTE
        )
    sources = [source for item in items for source in item.sources]
    return Field[list[GrowthSignal]](value=items, status="verified", sources=sources)


def _decision_key(name: str, domain_key: str, role: str | None) -> tuple[str, str, str]:
    return (squash(name), domain_key, squash(role))


def build_decision_makers(bundle: EvidenceBundle) -> Field[list[DecisionMaker]]:
    """Exa name+role pairs (verified) merged with MoltSets people
    (provider-reported), deduped on name + company key + role."""
    merged: dict[tuple[str, str, str], DecisionMaker] = {}
    order: list[tuple[str, str, str]] = []

    if bundle.leadership is not None:
        for hit in bundle.leadership.hits:
            if not hit.url or safe_url(hit.url) is None:
                continue
            for highlight in hit.highlights:
                cleaned = clean_text(highlight, MAX_HIGHLIGHT_LEN)
                if not cleaned:
                    continue
                for sentence in sentences(cleaned):
                    pair = extract_person_role(sentence, bundle.company_squash)
                    if pair is None:
                        continue
                    name, role = pair
                    key = _decision_key(name, bundle.domain_key, role)
                    maker = DecisionMaker(
                        name=clean_text(name, MAX_NAME_LEN) or name[:MAX_NAME_LEN],
                        title=clean_text(role, MAX_NAME_LEN),
                        status="verified",
                        sources=[
                            hit_source(hit, bundle.leadership, bundle.clock, highlight=highlight)
                        ],
                        mock=bundle.leadership.mock,
                    )
                    if key in merged:
                        merged[key].sources.extend(
                            s for s in maker.sources if s not in merged[key].sources
                        )
                        merged[key].mock = merged[key].mock or maker.mock
                    else:
                        merged[key] = maker
                        order.append(key)

    people_result = bundle.moltsets_people
    if people_result is not None:
        for person in people_result.people:
            person_name = clean_text(person.full_name, MAX_NAME_LEN)
            if not person_name:
                continue
            title = clean_text(person.title, MAX_NAME_LEN)
            key = _decision_key(person_name, bundle.domain_key, title)
            source = moltsets_source(people_result, bundle.clock)
            if key in merged:
                existing = merged[key]
                existing.sources.extend(s for s in [source] if s not in existing.sources)
                existing.mock = existing.mock or people_result.mock
                if existing.title is None:
                    existing.title = title
                if existing.seniority is None:
                    existing.seniority = clean_text(person.seniority, MAX_NAME_LEN)
                if existing.profile_url is None:
                    existing.profile_url = safe_url(person.linkedin_url)
            else:
                merged[key] = DecisionMaker(
                    name=person_name,
                    title=title,
                    seniority=clean_text(person.seniority, MAX_NAME_LEN),
                    profile_url=person.linkedin_url,
                    status="provider-reported",
                    sources=[source],
                    mock=people_result.mock,
                )
                order.append(key)

    items = [merged[key] for key in order]
    if not items:
        return Field[list[DecisionMaker]](value=[], status="unavailable", sources=[])
    all_verified = all(item.status == "verified" for item in items)
    status = "verified" if all_verified else "provider-reported"
    sources = [source for item in items for source in item.sources]
    return Field[list[DecisionMaker]](value=items, status=status, sources=sources)


def build_contact_information(bundle: EvidenceBundle) -> Field[list[ContactInfo]]:
    """MoltSets-returned business contact details only — never inferred."""
    people_result = bundle.moltsets_people
    items: list[ContactInfo] = []
    if people_result is not None:
        for person in people_result.people:
            name = clean_text(person.full_name, MAX_NAME_LEN)
            if not name:
                continue
            email = clean_text(person.business_email, MAX_EMAIL_LEN)
            grade = clean_text(person.business_email_risk_score, MAX_NAME_LEN)
            validated = clean_text(person.business_email_validated_at, MAX_NAME_LEN)
            if not (email or grade or validated):
                continue  # no contact data returned -> no row
            items.append(
                ContactInfo(
                    person_name=name,
                    business_email=email,
                    email_risk_grade=grade,
                    email_last_validated=validated,
                    status="provider-reported",
                    sources=[moltsets_source(people_result, bundle.clock)],
                    mock=people_result.mock,
                )
            )
    if not items:
        return Field[list[ContactInfo]](value=[], status="unavailable", sources=[])
    sources = [source for item in items for source in item.sources]
    return Field[list[ContactInfo]](value=items, status="provider-reported", sources=sources)


def build_hypothesis(
    objective: str | None,
    fields: "NormalizedFields",
    company_name: str | None,
) -> Field[GtmHypothesis]:
    """Deterministic, cautious GTM hypothesis (tech section 9).

    Built only when the objective is non-blank AND there is >=1 sourced
    growth signal or a sourced description. Rationale carries the items' own
    sources; status is always ``inferred``.
    """
    signals = fields.growth_signals.value or []
    description = fields.description
    if not (objective and objective.strip()) or not (signals or description.value):
        return Field[GtmHypothesis](
            value=None, status="unavailable", sources=[], note=NO_HYPOTHESIS_NOTE
        )

    subject = company_name or "the company"
    first_summary = signals[0].summary if signals else (description.value or "")
    summary = (
        f"Given the stated objective, {subject} may be a potential fit because "
        f"public sources report: {first_summary} "
        "This is a hypothesis, not confirmed buying intent."
    )
    summary = clean_text(summary, 400) or summary[:400]

    rationale: list[RationalePoint] = [
        RationalePoint(point=signal.summary, sources=signal.sources) for signal in signals[:2]
    ]
    if description.value and len(rationale) < 3:
        rationale.append(RationalePoint(point=description.value, sources=description.sources))
    rationale = rationale[:3]

    return Field[GtmHypothesis](
        value=GtmHypothesis(
            summary=summary,
            rationale=rationale,
            caveat=HYPOTHESIS_CAVEAT,
        ),
        status="inferred",
        sources=[],
    )


@dataclass
class NormalizedFields:
    """All user-facing report fields produced by normalization."""

    description: Field[str]
    industry: Field[str]
    target_market: Field[str]
    employee_size: Field[str]
    growth_signals: Field[list[GrowthSignal]]
    decision_makers: Field[list[DecisionMaker]]
    contact_information: Field[list[ContactInfo]]
    potential_gtm_opportunity: Field[GtmHypothesis]


def normalize_fields(
    bundle: EvidenceBundle,
    objective: str | None,
    company_name: str | None,
    warnings: list[str],
) -> NormalizedFields:
    """Build every factual field plus the GTM hypothesis."""
    description = build_description(bundle)
    industry = build_industry(bundle, warnings)
    target_market = build_target_market(bundle)
    employee_size = build_employee_size(bundle)
    growth_signals = build_growth_signals(bundle)
    decision_makers = build_decision_makers(bundle)
    contact_information = build_contact_information(bundle)

    partial = NormalizedFields(
        description=description,
        industry=industry,
        target_market=target_market,
        employee_size=employee_size,
        growth_signals=growth_signals,
        decision_makers=decision_makers,
        contact_information=contact_information,
        potential_gtm_opportunity=Field[GtmHypothesis](
            value=None, status="unavailable", sources=[]
        ),
    )
    partial.potential_gtm_opportunity = build_hypothesis(objective, partial, company_name)
    return partial
