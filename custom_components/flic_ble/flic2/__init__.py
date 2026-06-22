"""
Flic 2 BLE Protocol Library.

A Python library for communicating with Flic 2 buttons via Bluetooth Low Energy.

Usage:
    from flic2 import Flic2Client, ButtonEventType

    async def main():
        client = Flic2Client()

        # Discover buttons
        buttons = await client.scan(timeout=10.0)
        if not buttons:
            print("No Flic 2 buttons found")
            return

        # Connect
        await client.connect(buttons[0].address)

        # Pair (first time) or quick verify (reconnection)
        if not client.has_stored_credentials():
            await client.pair()
        else:
            await client.quick_verify()

        # Handle events
        def on_event(event):
            if event.event_type == ButtonEventType.SINGLE_CLICK:
                print("Single click!")
            elif event.event_type == ButtonEventType.DOUBLE_CLICK:
                print("Double click!")
            elif event.event_type == ButtonEventType.HOLD:
                print("Hold!")

        client.on_button_event = on_event
        await client.listen()

    import asyncio
    asyncio.run(main())
"""

__version__ = "0.1.0"

# Main client
from .connection.client import Flic2Client
from .connection.scanner import Flic2Scanner, discover_flic2_buttons

# Models
from .models import (
    ButtonEvent,
    ButtonEventType,
    ButtonInfo,
    ConnectionState,
    PairingCredentials,
)

# Storage
from .storage.database import CredentialStorage

# Exceptions
from .exceptions import (
    Flic2Error,
    ConnectionError,
    PairingError,
    InvalidVerifierError,
    InvalidSignatureError,
    ProtocolError,
    TimeoutError,
    StorageError,
    NotPairedError,
)

# Constants
from .const import (
    FLIC2_SERVICE_UUID,
    FLIC2_WRITE_UUID,
    FLIC2_NOTIFY_UUID,
)

__all__ = [
    # Version
    "__version__",
    # Client
    "Flic2Client",
    "Flic2Scanner",
    "discover_flic2_buttons",
    # Models
    "ButtonEvent",
    "ButtonEventType",
    "ButtonInfo",
    "ConnectionState",
    "PairingCredentials",
    # Storage
    "CredentialStorage",
    # Exceptions
    "Flic2Error",
    "ConnectionError",
    "PairingError",
    "InvalidVerifierError",
    "InvalidSignatureError",
    "ProtocolError",
    "TimeoutError",
    "StorageError",
    "NotPairedError",
    # Constants
    "FLIC2_SERVICE_UUID",
    "FLIC2_WRITE_UUID",
    "FLIC2_NOTIFY_UUID",
]
