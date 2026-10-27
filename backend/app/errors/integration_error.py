"""Domain errors raised by the integrations layer (integration plan §5.2).

Codes are stable and messages user-safe; the API layer maps them onto the
shared ``ErrorResponse`` shape like ``SheetError`` — e.g.
``hubspot_not_configured`` becomes 503, ``import_conflict`` becomes 409.
"""


class IntegrationError(Exception):
    """An integration business-rule or provider failure.

    ``code`` is machine-readable (``hubspot_not_configured``,
    ``hubspot_not_connected``, ``oauth_state_mismatch``,
    ``oauth_exchange_failed``, ``hubspot_auth``, ``hubspot_forbidden``,
    ``hubspot_not_found``, ``hubspot_rate_limited``,
    ``hubspot_server_error``, ``hubspot_timeout``, ``hubspot_network``,
    ``hubspot_invalid_response``, ``import_conflict``); ``message`` is
    safe to show — never tokens, keys, or raw provider bodies.
    """

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
