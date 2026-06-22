"""Packet encoding and decoding for Flic 2 protocol."""

import struct
from dataclasses import dataclass
from typing import Optional, List, Tuple

from .opcodes import Opcode
from ..const import SIGNATURE_LENGTH, CONN_ID_MASK, NEWLY_ASSIGNED_BIT, MULTI_BIT, FRAGMENT_BIT
from ..crypto import ChaskeyLTS
from ..models import ButtonEventType, ButtonEvent


@dataclass
class Packet:
    """Decoded packet."""
    conn_id: int
    newly_assigned: bool
    is_multi: bool
    is_fragment: bool
    opcode: int
    payload: bytes
    signature: Optional[bytes] = None

    @property
    def header_byte(self) -> int:
        """Reconstruct header byte."""
        # Header format: conn_id in bits 0-4, flags in bits 5-7
        header = self.conn_id & CONN_ID_MASK
        if self.newly_assigned:
            header |= NEWLY_ASSIGNED_BIT
        if self.is_multi:
            header |= MULTI_BIT
        if self.is_fragment:
            header |= FRAGMENT_BIT
        return header


class PacketEncoder:
    """Encodes packets for transmission."""

    def __init__(self, session_key: Optional[bytes] = None):
        self.session_key = session_key
        self._chaskey: Optional[ChaskeyLTS] = None
        if session_key:
            self._chaskey = ChaskeyLTS(session_key)

    def set_session_key(self, key: bytes):
        """Set session key for signing packets."""
        self.session_key = key
        self._chaskey = ChaskeyLTS(key)

    def encode(
        self,
        opcode: int,
        payload: bytes,
        conn_id: int = 0,
        newly_assigned: bool = False,
        sign: bool = False,
    ) -> bytes:
        """
        Encode a packet.

        Args:
            opcode: Packet opcode
            payload: Packet payload
            conn_id: Connection ID (0-31)
            newly_assigned: Whether this is a newly assigned connection
            sign: Whether to add Chaskey signature

        Returns:
            Encoded packet bytes
        """
        # Build header byte - conn_id in bits 0-4, flags in bits 5-7
        header = conn_id & CONN_ID_MASK
        if newly_assigned:
            header |= NEWLY_ASSIGNED_BIT

        # Build packet
        packet = bytes([header, opcode]) + payload

        # Add signature if requested
        if sign and self._chaskey:
            signature = self._chaskey.mac5(packet)
            packet = packet + signature

        return packet

    def encode_full_verify_request_1(self, tmp_id: bytes) -> bytes:
        """Encode FullVerifyRequest1."""
        if len(tmp_id) != 4:
            raise ValueError(f"tmp_id must be 4 bytes, got {len(tmp_id)}")
        return self.encode(Opcode.FULL_VERIFY_REQUEST_1, tmp_id)

    def encode_full_verify_request_2(
        self,
        our_pubkey: bytes,
        client_random: bytes,
        verifier: bytes,
        conn_id: int = 0,
        rfu: int = 0,
    ) -> bytes:
        """
        Encode FullVerifyRequest2.

        Per the official Flic 2 Protocol Specification:
        struct FullVerifyRequest2 {
            uint8_t opcode;
            uint8_t ecdh_public_key[32];
            uint8_t random_bytes[8];
            uint8_t rfu;  // reserved for future use
            uint8_t verifier[16];
        };

        Args:
            our_pubkey: 32-byte X25519 public key
            client_random: 8-byte random
            verifier: 16-byte verifier
            conn_id: Connection ID
            rfu: Reserved for future use (default 0)

        Returns:
            Encoded packet
        """
        if len(our_pubkey) != 32:
            raise ValueError(f"pubkey must be 32 bytes, got {len(our_pubkey)}")
        if len(client_random) != 8:
            raise ValueError(f"client_random must be 8 bytes, got {len(client_random)}")
        if len(verifier) != 16:
            raise ValueError(f"verifier must be 16 bytes, got {len(verifier)}")

        # Format per official spec:
        # pubkey(32) + random(8) + rfu(1) + verifier(16) = 57 bytes
        payload = our_pubkey + client_random + bytes([rfu]) + verifier
        return self.encode(Opcode.FULL_VERIFY_REQUEST_2, payload, conn_id=conn_id)

    def encode_quick_verify_request(
        self,
        pairing_id: bytes,
        client_random: bytes,
        tmp_id: bytes,
        flags: int = 0,
    ) -> bytes:
        """
        Encode QuickVerifyRequest.

        Format per Flic 2 protocol:
        - client_random (7 bytes)
        - flags (1 byte)
        - tmp_id (4 bytes)
        - pairing_id (4 bytes)

        Args:
            pairing_id: 4-byte pairing ID
            client_random: 8-byte random (only first 7 bytes used)
            tmp_id: 4-byte temporary ID
            flags: Flags byte (default 0)

        Returns:
            Encoded packet
        """
        if len(pairing_id) != 4:
            raise ValueError(f"pairing_id must be 4 bytes, got {len(pairing_id)}")
        if len(client_random) < 7:
            raise ValueError(f"client_random must be at least 7 bytes, got {len(client_random)}")
        if len(tmp_id) != 4:
            raise ValueError(f"tmp_id must be 4 bytes, got {len(tmp_id)}")

        # Format: client_random(7) + flags(1) + tmp_id(4) + pairing_id(4)
        payload = client_random[:7] + bytes([flags]) + tmp_id + pairing_id
        return self.encode(Opcode.QUICK_VERIFY_REQUEST, payload)

    def encode_ping(self, conn_id: int = 0) -> bytes:
        """Encode ping request."""
        return self.encode(Opcode.PING_REQUEST, b"", conn_id=conn_id, sign=True)


class PacketDecoder:
    """Decodes received packets."""

    def __init__(self, session_key: Optional[bytes] = None):
        self.session_key = session_key
        self._chaskey: Optional[ChaskeyLTS] = None
        if session_key:
            self._chaskey = ChaskeyLTS(session_key)

        # Fragmentation reassembly
        self._fragment_buffer: bytes = b""

    def set_session_key(self, key: bytes):
        """Set session key for verifying signatures."""
        self.session_key = key
        self._chaskey = ChaskeyLTS(key)

    def decode(self, data: bytes, verify_signature: bool = False) -> Packet:
        """
        Decode a packet.

        Args:
            data: Raw packet bytes
            verify_signature: Whether to verify Chaskey signature

        Returns:
            Decoded Packet

        Raises:
            InvalidSignatureError: If signature verification fails
        """
        from ..exceptions import InvalidSignatureError

        if len(data) < 2:
            raise ValueError(f"Packet too short: {len(data)} bytes")

        header = data[0]
        opcode = data[1]

        conn_id = header & CONN_ID_MASK  # conn_id is in bits 0-4, no shift needed
        newly_assigned = bool(header & NEWLY_ASSIGNED_BIT)
        is_multi = bool(header & MULTI_BIT)
        is_fragment = bool(header & FRAGMENT_BIT)

        # Handle signature
        signature = None
        if verify_signature and self._chaskey and len(data) > SIGNATURE_LENGTH + 2:
            signature = data[-SIGNATURE_LENGTH:]
            payload_with_header = data[:-SIGNATURE_LENGTH]

            # Verify signature
            expected_sig = self._chaskey.mac5(payload_with_header)
            if signature != expected_sig:
                raise InvalidSignatureError("Packet signature mismatch")

            payload = data[2:-SIGNATURE_LENGTH]
        else:
            payload = data[2:]

        return Packet(
            conn_id=conn_id,
            newly_assigned=newly_assigned,
            is_multi=is_multi,
            is_fragment=is_fragment,
            opcode=opcode,
            payload=payload,
            signature=signature,
        )

    def decode_full_verify_response_1(
        self,
        payload: bytes,
    ) -> Tuple[bytes, bytes, int, bytes, bytes]:
        """
        Decode FullVerifyResponse1.

        Expected format: tmp_id(4) + signature(64) + address(6) + address_type(1) + pubkey(32) + random(8) [+ flags(1)]
        Minimum: 115 bytes (without flags)

        Returns:
            Tuple of (signature, address, address_type, ecdh_pubkey, button_random)
        """
        import logging
        _LOGGER = logging.getLogger(__name__)

        _LOGGER.debug(f"FullVerifyResponse1 payload length: {len(payload)} bytes")
        _LOGGER.debug(f"FullVerifyResponse1 payload hex: {payload.hex()}")

        # Check minimum length: tmp_id(4) + sig(64) + addr(6) + type(1) + pubkey(32) + random(8) = 115
        if len(payload) < 115:
            raise ValueError(f"FullVerifyResponse1 too short: {len(payload)} bytes, need at least 115")

        # Skip tmp_id echo (4 bytes)
        offset = 4
        _LOGGER.debug(f"tmp_id echo: {payload[:4].hex()}")

        signature = bytes(payload[offset:offset + 64])
        offset += 64
        _LOGGER.debug(f"signature: {signature.hex()}")

        address = bytes(payload[offset:offset + 6])
        offset += 6
        _LOGGER.debug(f"address: {address.hex()}")

        address_type = payload[offset]
        offset += 1
        _LOGGER.debug(f"address_type: {address_type}")

        ecdh_pubkey = bytes(payload[offset:offset + 32])
        offset += 32
        _LOGGER.debug(f"ecdh_pubkey: {ecdh_pubkey.hex()}")

        button_random = bytes(payload[offset:offset + 8])
        _LOGGER.debug(f"button_random: {button_random.hex()}")

        return signature, address, address_type, ecdh_pubkey, button_random

    def decode_full_verify_response_2(
        self,
        payload: bytes,
    ) -> Tuple[str, str, str, int, int]:
        """
        Decode FullVerifyResponse2 (button info).

        Format per Flic 2 protocol:
        - uuid(16) + flags(1) + name_len(1) + name(24 padded) + firmware(4) + battery(1) + serial(var)

        Returns:
            Tuple of (uuid, name, serial_number, firmware_version, battery_level)
        """
        import logging
        _LOGGER = logging.getLogger(__name__)

        if len(payload) < 18:
            raise ValueError(f"FullVerifyResponse2 too short: {len(payload)} bytes")

        # UUID is first 16 bytes
        uuid_bytes = payload[0:16]
        uuid_str = uuid_bytes.hex()

        # Format as standard UUID
        uuid_formatted = f"{uuid_str[:8]}-{uuid_str[8:12]}-{uuid_str[12:16]}-{uuid_str[16:20]}-{uuid_str[20:32]}"

        offset = 16

        # Skip flags byte (observed as 0xf6)
        offset += 1

        # Name length and name
        name_len = payload[offset]
        offset += 1
        name_raw = payload[offset:offset + name_len]
        name = name_raw.decode("utf-8", errors="replace").rstrip("\x00")
        offset += name_len

        # Skip name padding (24 - name_len bytes of zeros)
        padding_len = 24 - name_len
        if padding_len > 0:
            offset += padding_len

        # Firmware version (4 bytes, little-endian)
        firmware_version = 0
        if offset + 4 <= len(payload):
            firmware_version = struct.unpack("<I", payload[offset:offset + 4])[0]
            offset += 4

        # Battery level (1 byte)
        battery_level = 0
        if offset < len(payload):
            battery_level = payload[offset]
            offset += 1

        # Skip unknown byte (observed as 0x03)
        if offset < len(payload):
            offset += 1

        # Serial number (null-terminated string)
        serial_number = ""
        if offset < len(payload):
            serial_end = payload.find(b"\x00", offset)
            if serial_end == -1:
                serial_end = len(payload)
            # Also check for non-printable end marker
            for i in range(offset, min(serial_end, len(payload))):
                if payload[i] < 0x20 or payload[i] > 0x7e:
                    serial_end = i
                    break
            serial_number = payload[offset:serial_end].decode("utf-8", errors="replace")

        _LOGGER.debug(
            "Decoded FullVerifyResponse2: uuid=%s, name=%s, serial=%s, fw=%d, battery=%d",
            uuid_formatted, name, serial_number, firmware_version, battery_level
        )

        return uuid_formatted, name, serial_number, firmware_version, battery_level

    def decode_quick_verify_response(self, payload: bytes) -> bytes:
        """
        Decode QuickVerifyResponse.

        Returns:
            button_random (8 bytes)
        """
        if len(payload) < 8:
            raise ValueError(f"QuickVerifyResponse too short: {len(payload)} bytes")
        return payload[0:8]

    def decode_button_event(self, payload: bytes) -> List[ButtonEvent]:
        """
        Decode button event notification payload.

        Format (per official Flic 2 protocol):
        - press_counter: 4 bytes, little-endian
        - events: array of 7-byte entries:
          - timestamp: 6 bytes (32768 Hz ticks)
          - event_info: 1 byte (event_encoded + flags)

        Returns:
            List of ButtonEvent objects
        """
        import logging
        _LOGGER = logging.getLogger(__name__)

        events = []

        if len(payload) < 4:
            return events

        press_counter = int.from_bytes(payload[0:4], 'little')

        # Each event is 7 bytes: timestamp(6) + event_info(1)
        offset = 4
        while offset + 7 <= len(payload):
            timestamp = int.from_bytes(payload[offset:offset + 6], 'little')
            event_info = payload[offset + 6]
            offset += 7

            event_encoded = event_info & 0x0F
            was_queued = bool((event_info >> 4) & 0x01)

            # Decode event type based on encoding (per official protocol)
            # If bit 3 is set, it's a button up with additional info
            if (event_encoded >> 3) != 0:
                # Button up with click/hold info
                if event_encoded & 0x04:
                    event_type = ButtonEventType.HOLD
                elif event_encoded & 0x02:
                    if event_encoded & 0x01:
                        event_type = ButtonEventType.DOUBLE_CLICK
                    else:
                        event_type = ButtonEventType.SINGLE_CLICK
                else:
                    event_type = ButtonEventType.UP
            else:
                # Simple event types
                event_map = {
                    0: ButtonEventType.UP,
                    1: ButtonEventType.DOWN,
                    2: ButtonEventType.CLICK,
                    3: ButtonEventType.SINGLE_CLICK,
                    4: ButtonEventType.DOUBLE_CLICK,
                    5: ButtonEventType.HOLD,
                }
                event_type = event_map.get(event_encoded, ButtonEventType.UP)

            # Calculate age from timestamp (32768 Hz clock)
            age_seconds = 0.0  # Would need init_timestamp to calculate properly

            _LOGGER.debug(
                "Decoded event: type=%s, queued=%s, counter=%d, raw_info=0x%02x",
                event_type.name, was_queued, press_counter, event_info
            )

            events.append(ButtonEvent(
                event_type=event_type,
                was_queued=was_queued,
                age_seconds=age_seconds,
                press_counter=press_counter,
            ))

        return events

    def decode_init_button_events_response(self, payload: bytes) -> Tuple[int, int, int, int]:
        """
        Decode InitButtonEventsResponse payload.

        Format (per Flic 2 protocol):
        - boot_id: 4 bytes, little-endian
        - event_count: 4 bytes, little-endian
        - timestamp_hi: 4 bytes, little-endian
        - battery_level: 1 byte (percentage 0-100)

        Returns:
            Tuple of (boot_id, event_count, timestamp_hi, battery_level)
        """
        import logging
        _LOGGER = logging.getLogger(__name__)

        if len(payload) < 13:
            _LOGGER.warning(
                "InitButtonEventsResponse payload too short: %d bytes, expected at least 13. Payload: %s",
                len(payload), payload.hex()
            )
            return 0, 0, 0, 0

        boot_id = int.from_bytes(payload[0:4], 'little')
        event_count = int.from_bytes(payload[4:8], 'little')
        timestamp_hi = int.from_bytes(payload[8:12], 'little')
        battery_level = payload[12]

        _LOGGER.debug(
            "InitButtonEventsResponse: boot_id=%d, event_count=%d, timestamp_hi=%d, battery=%d%%",
            boot_id, event_count, timestamp_hi, battery_level
        )

        return boot_id, event_count, timestamp_hi, battery_level

    def decode_battery_status(self, payload: bytes) -> int:
        """
        Decode battery status.

        Returns:
            Battery level (0-100)
        """
        if len(payload) < 1:
            raise ValueError("Battery status payload empty")
        return payload[0]
