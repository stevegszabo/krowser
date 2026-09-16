class ScannerUnavailableError(RuntimeError):
    """Raised when a configured scanner binary isn't found on PATH."""


class ScanFailedError(RuntimeError):
    """Raised when a scanner ran but failed, timed out, or returned output
    that couldn't be parsed."""
