"""Exa request builder for list-building row generation (P9a).

Pure builder, no I/O — mirrors :mod:`app.research.cell_query`;
:mod:`app.research.queries` stays untouched.

One ``category="company"`` ``/search`` per generation call: the user types
a company-finding query ("robotics suppliers in Germany") and the raw hits
become candidate rows. Unlike :func:`identity_query` this is OPEN-web
list-building — NO ``include_domains``, NO date filters (the company
category rejects them with a 400 — docs/p0-openapi-notes.md), and NO
``output_schema``/``system_prompt`` (we want hits, not synthesized
output).
"""

from app.providers.types import ExaSearchRequest


def generate_rows_request(query: str, max_results: int) -> ExaSearchRequest:
    """The single company-category search behind ``POST …/rows/generate``.

    ``num_results`` stays within the shared ≤5 ``ExaSearchRequest`` cap
    (documented: finding more companies means repeated calls).
    """
    return ExaSearchRequest(
        query=query,
        num_results=max_results,
        category="company",
        search_type="auto",
    )
