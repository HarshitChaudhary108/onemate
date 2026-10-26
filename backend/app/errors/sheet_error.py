"""Domain errors raised by the sheets service layer.

Codes are stable and messages user-safe; the API layer (P5) maps them to
the shared ``ErrorResponse`` shape — name/reference conflicts become 409.
"""


class SheetError(Exception):
    """A sheets business-rule violation.

    ``code`` is machine-readable (``name_conflict``, ``column_referenced``,
    ``has_dependents``, ``row_limit``, ``column_limit``, ``import_limit``,
    ``invalid_template``, ``invalid_domain``, ``reserved_name``,
    ``invalid_column``, ``not_found``); ``message`` is safe to show.
    """

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
