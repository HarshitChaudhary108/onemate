"""Name↔domain agreement rules in ``resolve_identity`` (orchestrator).

The gate's false-negative class under test: a hit on the claimed domain
whose page title belongs to a product/subsidiary still corroborates the
pair when the name's distinctive tokens live in the host labels —
``store.google.com`` carries "google" even when the title says "Nest
Labs". Legal-form tokens (LLC/Inc/Corp/…) never decide a match.
"""

from conftest import utcnow

from app.normalize.domain import normalize_domain
from app.providers.types import ExaHit, ExaSearchResult
from app.research.orchestrator import resolve_identity


def _hit(url: str | None, title: str | None = None) -> ExaHit:
    return ExaHit(url=url, title=title)


def _result(*hits: ExaHit) -> ExaSearchResult:
    return ExaSearchResult(hits=list(hits), query="identity", calls=1)


# -- name + domain ------------------------------------------------------------


def test_subdomain_hit_name_in_parent_label_resolves() -> None:
    """Google LLC + google.com: only a product page on a subdomain is
    indexed — the brand token sits in the host, not the title."""
    resolution = resolve_identity(
        "Google LLC",
        normalize_domain("google.com"),
        _result(_hit("https://store.google.com/in/category/connected_home", "Nest Labs")),
        utcnow,
    )
    assert resolution.resolved is True
    assert resolution.domain_key == "google.com"


def test_legal_suffix_never_blocks_a_match() -> None:
    """'Acme Corp' + acme.test titled 'Acme Inc' — the distinctive token
    is all the evidence required; entity-form words are noise."""
    resolution = resolve_identity(
        "Acme Corp",
        normalize_domain("acme.test"),
        _result(_hit("https://acme.test/", "Acme Inc")),
        utcnow,
    )
    assert resolution.resolved is True
    assert resolution.domain_key == "acme.test"


def test_mismatched_name_and_domain_stays_ambiguous() -> None:
    """Apple + samsung.com: domain evidence exists but no hit carries the
    claimed name anywhere — title or host."""
    resolution = resolve_identity(
        "Apple",
        normalize_domain("samsung.com"),
        _result(
            _hit("https://www.samsung.com/", "Samsung Electronics"),
            _hit("https://www.samsung.com/us/", "Samsung US"),
        ),
        utcnow,
    )
    assert resolution.resolved is False
    assert resolution.note == "supplied name and domain do not agree"


def test_partial_name_overlap_stays_ambiguous() -> None:
    """Delta Airlines + deltafaucet.com: one shared token is not
    agreement — every distinctive token must be evidenced."""
    resolution = resolve_identity(
        "Delta Airlines",
        normalize_domain("deltafaucet.com"),
        _result(_hit("https://deltafaucet.com/", "Delta Faucet")),
        utcnow,
    )
    assert resolution.resolved is False
    assert resolution.note == "supplied name and domain do not agree"


def test_no_domain_evidence_stays_unresolved() -> None:
    resolution = resolve_identity(
        "Acme",
        normalize_domain("acme.test"),
        _result(_hit("https://unrelated.example/", "Acme")),
        utcnow,
    )
    assert resolution.resolved is False
    assert resolution.note == "no evidence for this domain"


def test_title_match_still_resolves() -> None:
    """The classic path is untouched: squashed name inside the title."""
    resolution = resolve_identity(
        "Barry Callebaut",
        normalize_domain("barry-callebaut.com"),
        _result(_hit("https://barry-callebaut.com/", "Barry Callebaut Group")),
        utcnow,
    )
    assert resolution.resolved is True


def test_domain_only_ignores_hit_titles() -> None:
    """No name supplied → any domain-matching hit resolves, title
    notwithstanding."""
    resolution = resolve_identity(
        None,
        normalize_domain("google.com"),
        _result(_hit("https://store.google.com/", "Nest Labs")),
        utcnow,
    )
    assert resolution.resolved is True


def test_multi_token_name_split_across_labels_resolves() -> None:
    """'Apple Store' + store.apple.com: each token lives in its own
    label — together they corroborate the pair."""
    resolution = resolve_identity(
        "Apple Store",
        normalize_domain("apple.com"),
        _result(_hit("https://store.apple.com/", "Welcome")),
        utcnow,
    )
    assert resolution.resolved is True


# -- name only -----------------------------------------------------------------


def test_name_only_single_host_resolves() -> None:
    resolution = resolve_identity(
        "Acme",
        None,
        _result(
            _hit("https://acme.test/about", "Acme"),
            _hit("https://acme.test/", "Acme"),
        ),
        utcnow,
    )
    assert resolution.resolved is True
    assert resolution.domain_key == "acme.test"


def test_name_only_multiple_hosts_stays_ambiguous() -> None:
    resolution = resolve_identity(
        "Delta",
        None,
        _result(
            _hit("https://delta.com/", "Delta Air Lines"),
            _hit("https://deltafaucet.com/", "Delta Faucet"),
        ),
        utcnow,
    )
    assert resolution.resolved is False
    assert resolution.note == "top name-matching hits point to more than one host"
