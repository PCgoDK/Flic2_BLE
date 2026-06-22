"""Opcodes for Flic 2 BLE protocol."""

from enum import IntEnum


class Opcode(IntEnum):
    """Flic 2 protocol opcodes."""

    # Full Verify Flow (Pairing)
    FULL_VERIFY_REQUEST_1 = 0x00      # Client -> Button: tmp_id
    FULL_VERIFY_RESPONSE_1 = 0x00     # Button -> Client: signature, address, pubkey, random
    FULL_VERIFY_FAIL_RESPONSE_1 = 0x01  # Button -> Client: fail
    FULL_VERIFY_REQUEST_2 = 0x02      # Client -> Button: pubkey, random, verifier
    FULL_VERIFY_RESPONSE_2 = 0x01     # Button -> Client: button info (after session established)
    FULL_VERIFY_FAIL_RESPONSE_2 = 0x03  # Button -> Client: fail reason

    # Quick Verify Flow (Reconnection)
    QUICK_VERIFY_REQUEST = 0x05       # Client -> Button: pairing_id, random, tmp_id
    NO_PAIRING_EXISTS = 0x06          # Button -> Client: no pairing exists for given pairing_id
    QUICK_VERIFY_RESPONSE = 0x08      # Button -> Client: random
    QUICK_VERIFY_FAIL = 0x09          # Button -> Client: fail

    # Button Events Initialization
    INIT_BUTTON_EVENTS = 0x17         # Client -> Button: init event reception
    INIT_BUTTON_EVENTS_RESPONSE = 0x0A  # Button -> Client: events initialized
    INIT_BUTTON_EVENTS_NO_BOOT = 0x0B   # Button -> Client: events initialized (no boot)
    BUTTON_EVENT_NOTIFICATION = 0x0C  # Button -> Client: button event
    DISCONNECTED_LINK = 0x09          # Button -> Client: disconnected

    # Legacy event opcodes (may not be used)
    BUTTON_EVENT_SINGLE = 0x07        # Button -> Client: single event

    # Keep Alive
    PING_REQUEST = 0x0E               # Client -> Button
    PING_RESPONSE = 0x0F              # Button -> Client

    # Button Info
    GET_BUTTON_INFO = 0x0E            # Client -> Button
    BUTTON_INFO_RESPONSE = 0x0F       # Button -> Client

    # Firmware Update
    FIRMWARE_UPDATE_START = 0x10
    FIRMWARE_UPDATE_DATA = 0x11
    FIRMWARE_UPDATE_COMPLETE = 0x12

    # Name
    SET_NAME = 0x13                   # Client -> Button
    SET_NAME_RESPONSE = 0x14          # Button -> Client

    # Connection Config
    SET_CONNECTION_PARAMS = 0x15
    CONNECTION_PARAMS_RESPONSE = 0x16

    # Advanced Events
    SET_ADVANCED_EVENT_CONFIG = 0x17
    ADVANCED_EVENT = 0x18

    # Disconnect
    FORCE_DISCONNECT = 0x1A
    DISCONNECT_VERIFIED = 0x1B


class FullVerifyFailReason(IntEnum):
    """Reasons for full verify failure."""
    UNKNOWN = 0
    INVALID_VERIFIER = 1
    NOT_IN_PUBLIC_MODE = 2
    TOO_MANY_PAIRINGS = 3
    NOT_IN_PAIRING_MODE = 4


class QuickVerifyFailReason(IntEnum):
    """Reasons for quick verify failure."""
    UNKNOWN = 0
    INVALID_PAIRING_ID = 1
    INVALID_SIGNATURE = 2
    NO_SPACE = 3
