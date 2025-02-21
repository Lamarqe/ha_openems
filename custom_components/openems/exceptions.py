"""openems.exceptions."""


class APIError(Exception):
    """HTTPError Exception Class."""

    def __init__(self, message: str, code: int):
        """__init__."""
        super().__init__(message)
        self.code = code
