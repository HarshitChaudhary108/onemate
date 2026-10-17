"""D7 scope-gate tests — the README boundary, standalone and deterministic."""

import pytest

from app.policy.scope import scope_violation


@pytest.mark.parametrize(
    "text",
    [
        "add a phone number column",
        "collect their phone numbers",
        "find the company's telephone",
        "get the mobile number for the account",
        "direct dial for the CTO",
    ],
    ids=["phone-column", "phones", "telephone", "mobile-number", "direct-dial"],
)
def test_phone_requests_violate(text: str) -> None:
    assert scope_violation(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "add the CEO's personal email",
        "find me the work email of the founder",
        "collect email addresses of employees",
        "guess the direct email for each company",
        "the contact email for the account owner",
    ],
    ids=["personal-email", "find-work-email", "email-addresses", "guess-email", "contact-email"],
)
def test_email_requests_violate(text: str) -> None:
    assert scope_violation(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "find the best contact at the company",
        "who to contact at the firm",
        "get the contact details for each account",
        "identify contacts at the company",
        "the decision-maker email",
    ],
    ids=["best-contact", "who-to-contact", "contact-details", "identify-contacts", "dm-email"],
)
def test_contact_inference_violates(text: str) -> None:
    assert scope_violation(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "score buying intent per company",
        "add a lead score column",
    ],
    ids=["buying-intent", "lead-score"],
)
def test_intent_scores_violate(text: str) -> None:
    assert scope_violation(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "add the company's latest funding round",
        "does the website have a contact page",
        "the number of employees",
        "summarize the careers page",
        "is the company hiring a mobile engineer",
        "does the pricing page mention email support",
    ],
    ids=[
        "funding",
        "contact-page",
        "employee-count",
        "careers",
        "mobile-engineer",
        "email-support",
    ],
)
def test_benign_requests_pass(text: str) -> None:
    assert scope_violation(text) is None


def test_reason_is_user_safe() -> None:
    reason = scope_violation("add a phone column")
    assert reason is not None and "phone" in reason
