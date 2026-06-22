"""Custom exceptions for Flic 2 BLE protocol."""


class Flic2Error(Exception):
    """Base exception for Flic 2 errors."""


class ConnectionError(Flic2Error):
    """Error during BLE connection."""


class PairingError(Flic2Error):
    """Error during pairing process."""


class InvalidVerifierError(PairingError):
    """Button rejected our verifier."""


class InvalidSignatureError(Flic2Error):
    """Invalid Ed25519 or Chaskey signature."""


class ProtocolError(Flic2Error):
    """Protocol-level error."""


class TimeoutError(Flic2Error):
    """Operation timed out."""


class StorageError(Flic2Error):
    """Error accessing credential storage."""


class NotPairedError(Flic2Error):
    """Attempted operation requiring pairing without being paired."""
