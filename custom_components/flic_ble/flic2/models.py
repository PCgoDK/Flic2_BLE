"""Data models for Flic 2 BLE protocol."""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional
import time


class ButtonEventType(IntEnum):
    """
    Button event types.

    These correspond to Flic2EventButtonEventType in the official SDK.
    """
    UP = 0              # Button was released
    DOWN = 1            # Button was pressed
    CLICK = 2           # Button was clicked (held <= 1 second)
    SINGLE_CLICK = 3    # Single click (no double click followed)
    DOUBLE_CLICK = 4    # Double click (two presses within 0.5 seconds)
    HOLD = 5            # Button held for >= 1 second


class ButtonEventClass(IntEnum):
    """
    Button event class - determines which events are emitted.

    These correspond to Flic2EventButtonEventClass in the official SDK.
    """
    UP_OR_DOWN = 0                      # Triggers on every press/release
    CLICK_OR_HOLD = 1                   # Distinguishes click vs hold
    SINGLE_OR_DOUBLE_CLICK = 2          # Distinguishes single vs double click
    SINGLE_OR_DOUBLE_CLICK_OR_HOLD = 3  # All three: single, double, hold


class ConnectionState(IntEnum):
    """Connection state."""
    DISCONNECTED = 0
    CONNECTING = 1
    CONNECTED = 2
    PAIRING = 3
    PAIRED = 4
    QUICK_VERIFYING = 5
    READY = 6


@dataclass
class ButtonEvent:
    """Represents a button event."""
    event_type: ButtonEventType
    was_queued: bool
    age_seconds: float = 0.0
    press_counter: int = 0
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        queued_str = f", age={self.age_seconds:.2f}s" if self.was_queued else ""
        return f"ButtonEvent({self.event_type.name}{queued_str})"


@dataclass
class ButtonInfo:
    """Information about a Flic 2 button."""
    address: str
    uuid: str
    name: str
    serial_number: str
    firmware_version: int
    battery_level: Optional[int] = None
    color: Optional[str] = None

    def __str__(self) -> str:
        return f"Flic2Button({self.name}, {self.address})"


@dataclass
class PairingCredentials:
    """Stored pairing credentials."""
    address: str
    pairing_id: bytes
    pairing_key: bytes
    button_uuid: str
    name: str
    serial_number: str
    firmware_version: int
    last_boot_id: Optional[int] = None
    last_event_count: Optional[int] = None

    def __post_init__(self):
        if isinstance(self.pairing_id, str):
            self.pairing_id = bytes.fromhex(self.pairing_id)
        if isinstance(self.pairing_key, str):
            self.pairing_key = bytes.fromhex(self.pairing_key)


@dataclass
class SessionState:
    """Current session state."""
    conn_id: int = 0
    session_key: Optional[bytes] = None
    tx_counter: int = 0
    rx_counter: int = 0
    is_authenticated: bool = False
    is_paired: bool = False
    boot_id: Optional[int] = None
    event_count: int = 0

    def reset(self):
        """Reset session state."""
        self.conn_id = 0
        self.session_key = None
        self.tx_counter = 0
        self.rx_counter = 0
        self.is_authenticated = False
        self.is_paired = False
        self.boot_id = None
        self.event_count = 0
