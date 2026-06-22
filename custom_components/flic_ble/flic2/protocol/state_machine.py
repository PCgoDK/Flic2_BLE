"""Pairing state machine for Flic 2 protocol."""

import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Callable, Awaitable, List

from .opcodes import Opcode, FullVerifyFailReason, QuickVerifyFailReason
from .packets import PacketEncoder, PacketDecoder, Packet
from ..crypto import (
    generate_keypair,
    compute_shared_secret,
    derive_full_verify_secret,
    derive_verifier,
    derive_session_key,
    derive_pairing_data,
    derive_quick_verify_session_key,
    verify_button_identity,
)
from ..crypto.keys import generate_random
from ..models import PairingCredentials, ButtonInfo
from ..exceptions import PairingError, InvalidVerifierError, InvalidSignatureError


_LOGGER = logging.getLogger(__name__)


class PairingState(Enum):
    """Pairing state machine states."""
    IDLE = auto()

    # Full verify states
    FULL_VERIFY_REQUEST_1_SENT = auto()
    FULL_VERIFY_RESPONSE_1_RECEIVED = auto()
    FULL_VERIFY_REQUEST_2_SENT = auto()
    FULL_VERIFY_COMPLETE = auto()

    # Quick verify states
    QUICK_VERIFY_REQUEST_SENT = auto()
    QUICK_VERIFY_COMPLETE = auto()

    # Error states
    FAILED = auto()


@dataclass
class PairingContext:
    """Context for pairing state machine."""
    # Temporary ID
    tmp_id: bytes = field(default_factory=lambda: generate_random(4))

    # Our keypair
    our_private_key: Optional[bytes] = None
    our_public_key: Optional[bytes] = None

    # Button data from response 1
    button_signature: Optional[bytes] = None
    button_address: Optional[bytes] = None
    button_address_type: Optional[int] = None
    button_ecdh_pubkey: Optional[bytes] = None
    button_random: Optional[bytes] = None

    # Our random
    client_random: bytes = field(default_factory=lambda: generate_random(8))

    # Derived values
    sig_bits: Optional[int] = None
    shared_secret: Optional[bytes] = None
    full_verify_secret: Optional[bytes] = None
    verifier: Optional[bytes] = None
    session_key: Optional[bytes] = None
    pairing_id: Optional[bytes] = None
    pairing_key: Optional[bytes] = None

    # Button info from response 2
    button_uuid: Optional[str] = None
    button_name: Optional[str] = None
    button_serial: Optional[str] = None
    button_firmware: Optional[int] = None
    button_battery: Optional[int] = None

    # Connection ID assigned by button
    conn_id: int = 0

    # Error info
    error_reason: Optional[int] = None


class PairingStateMachine:
    """
    State machine for Flic 2 pairing process.

    Handles both full verify (initial pairing) and quick verify (reconnection).
    """

    def __init__(
        self,
        send_func: Callable[[bytes], Awaitable[None]],
        stored_credentials: Optional[PairingCredentials] = None,
    ):
        """
        Initialize pairing state machine.

        Args:
            send_func: Async function to send packets
            stored_credentials: Stored credentials for quick verify
        """
        self.send = send_func
        self.stored_credentials = stored_credentials

        self.state = PairingState.IDLE
        self.ctx = PairingContext()

        self.encoder = PacketEncoder()
        self.decoder = PacketDecoder()

        # Callbacks
        self.on_session_key: Optional[Callable[[bytes], None]] = None
        self.on_pairing_complete: Optional[Callable[[PairingCredentials, ButtonInfo], None]] = None
        self.on_quick_verify_complete: Optional[Callable[[bytes], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None

    async def start_full_verify(self):
        """Start full verify (pairing) process."""
        _LOGGER.debug("Starting full verify pairing")

        # Generate new keypair and random
        self.ctx = PairingContext()
        self.ctx.our_private_key, self.ctx.our_public_key = generate_keypair()

        # Send FullVerifyRequest1
        packet = self.encoder.encode_full_verify_request_1(self.ctx.tmp_id)
        await self.send(packet)

        self.state = PairingState.FULL_VERIFY_REQUEST_1_SENT
        _LOGGER.debug(f"Sent FullVerifyRequest1 with tmp_id={self.ctx.tmp_id.hex()}")

    async def start_quick_verify(self):
        """Start quick verify (reconnection) process."""
        if not self.stored_credentials:
            raise PairingError("No stored credentials for quick verify")

        _LOGGER.debug("Starting quick verify reconnection")

        self.ctx = PairingContext()

        # Send QuickVerifyRequest
        packet = self.encoder.encode_quick_verify_request(
            pairing_id=self.stored_credentials.pairing_id,
            client_random=self.ctx.client_random,
            tmp_id=self.ctx.tmp_id,
        )
        await self.send(packet)

        self.state = PairingState.QUICK_VERIFY_REQUEST_SENT
        _LOGGER.debug(f"Sent QuickVerifyRequest with pairing_id={self.stored_credentials.pairing_id.hex()}")

    async def handle_packet(self, data: bytes) -> bool:
        """
        Handle incoming packet.

        Args:
            data: Raw packet data

        Returns:
            True if pairing is complete, False if still in progress
        """
        packet = self.decoder.decode(data)
        _LOGGER.debug(f"Received packet: opcode={packet.opcode:#x}, state={self.state}")

        if self.state == PairingState.FULL_VERIFY_REQUEST_1_SENT:
            return await self._handle_full_verify_response_1(packet)

        elif self.state == PairingState.FULL_VERIFY_REQUEST_2_SENT:
            return await self._handle_full_verify_response_2(packet)

        elif self.state == PairingState.QUICK_VERIFY_REQUEST_SENT:
            return await self._handle_quick_verify_response(packet)

        return False

    async def _handle_full_verify_response_1(self, packet: Packet) -> bool:
        """Handle FullVerifyResponse1."""
        if packet.opcode == Opcode.FULL_VERIFY_FAIL_RESPONSE_1:
            self.state = PairingState.FAILED
            reason = packet.payload[0] if packet.payload else 0
            self.ctx.error_reason = reason
            error_msg = f"FullVerify failed at step 1: {FullVerifyFailReason(reason).name}"
            _LOGGER.error(error_msg)
            if self.on_error:
                self.on_error(error_msg)
            return False

        if packet.opcode != Opcode.FULL_VERIFY_RESPONSE_1:
            _LOGGER.warning(f"Unexpected opcode in state {self.state}: {packet.opcode:#x}")
            return False

        # Decode response
        try:
            (
                self.ctx.button_signature,
                self.ctx.button_address,
                self.ctx.button_address_type,
                self.ctx.button_ecdh_pubkey,
                self.ctx.button_random,
            ) = self.decoder.decode_full_verify_response_1(packet.payload)
        except Exception as e:
            _LOGGER.error(f"Failed to decode FullVerifyResponse1: {e}")
            self.state = PairingState.FAILED
            return False

        _LOGGER.debug(f"Button address: {self.ctx.button_address.hex()}")
        _LOGGER.debug(f"Button ECDH pubkey: {self.ctx.button_ecdh_pubkey.hex()}")
        _LOGGER.debug(f"Button random: {self.ctx.button_random.hex()}")

        # Check flags byte for public mode
        # flags is at offset 115 in payload (after random bytes)
        if len(packet.payload) > 115:
            flags = packet.payload[115]
            is_public_mode = (flags >> 1) & 0x01
            _LOGGER.debug(f"Button flags: {flags:#04x}, is_public_mode={is_public_mode}")
            if not is_public_mode:
                self.state = PairingState.FAILED
                error_msg = "Button is not in pairing mode. Hold the button for 8 seconds until the LED blinks rapidly, then try again."
                _LOGGER.error(error_msg)
                if self.on_error:
                    self.on_error(error_msg)
                return False

        # Verify Ed25519 signature and get sig_bits
        try:
            self.ctx.sig_bits = verify_button_identity(
                self.ctx.button_signature,
                self.ctx.button_address,
                self.ctx.button_address_type,
                self.ctx.button_ecdh_pubkey,
            )
            _LOGGER.debug(f"Ed25519 verified, sig_bits={self.ctx.sig_bits}")
        except InvalidSignatureError as e:
            _LOGGER.error(f"Ed25519 verification failed: {e}")
            self.state = PairingState.FAILED
            if self.on_error:
                self.on_error(str(e))
            return False

        # Compute shared secret
        self.ctx.shared_secret = compute_shared_secret(
            self.ctx.our_private_key,
            self.ctx.button_ecdh_pubkey,
        )
        _LOGGER.debug(f"Shared secret: {self.ctx.shared_secret.hex()}")

        # Derive keys
        self.ctx.full_verify_secret = derive_full_verify_secret(
            self.ctx.shared_secret,
            self.ctx.sig_bits,
            self.ctx.button_random,
            self.ctx.client_random,
        )
        _LOGGER.debug(f"Full verify secret: {self.ctx.full_verify_secret.hex()}")

        self.ctx.verifier = derive_verifier(self.ctx.full_verify_secret)
        self.ctx.session_key = derive_session_key(self.ctx.full_verify_secret)
        self.ctx.pairing_id, self.ctx.pairing_key = derive_pairing_data(self.ctx.full_verify_secret)

        _LOGGER.debug(f"Verifier: {self.ctx.verifier.hex()}")
        _LOGGER.debug(f"Session key: {self.ctx.session_key.hex()}")
        _LOGGER.debug(f"Pairing ID: {self.ctx.pairing_id.hex()}")
        _LOGGER.debug(f"Pairing key: {self.ctx.pairing_key.hex()}")

        # Store connection ID from response
        self.ctx.conn_id = packet.conn_id

        # Send FullVerifyRequest2
        packet_data = self.encoder.encode_full_verify_request_2(
            our_pubkey=self.ctx.our_public_key,
            client_random=self.ctx.client_random,
            verifier=self.ctx.verifier,
            conn_id=self.ctx.conn_id,
        )
        await self.send(packet_data)

        self.state = PairingState.FULL_VERIFY_REQUEST_2_SENT
        _LOGGER.debug("Sent FullVerifyRequest2")

        # Set session key for future packet verification
        self.decoder.set_session_key(self.ctx.session_key)
        self.encoder.set_session_key(self.ctx.session_key)

        if self.on_session_key:
            self.on_session_key(self.ctx.session_key)

        return False

    async def _handle_full_verify_response_2(self, packet: Packet) -> bool:
        """Handle FullVerifyResponse2."""
        if packet.opcode == Opcode.FULL_VERIFY_FAIL_RESPONSE_2:
            self.state = PairingState.FAILED
            reason = packet.payload[0] if packet.payload else 0
            self.ctx.error_reason = reason
            error_name = FullVerifyFailReason(reason).name if reason < 5 else f"UNKNOWN({reason})"
            error_msg = f"FullVerify failed at step 2: {error_name}"
            _LOGGER.error(error_msg)
            if reason == FullVerifyFailReason.INVALID_VERIFIER:
                raise InvalidVerifierError(error_msg)
            if self.on_error:
                self.on_error(error_msg)
            return False

        # Response 2 contains button info
        try:
            (
                self.ctx.button_uuid,
                self.ctx.button_name,
                self.ctx.button_serial,
                self.ctx.button_firmware,
                self.ctx.button_battery,
            ) = self.decoder.decode_full_verify_response_2(packet.payload)

            _LOGGER.info(f"Paired with button: {self.ctx.button_name} ({self.ctx.button_uuid})")
            _LOGGER.debug(f"Serial: {self.ctx.button_serial}, FW: {self.ctx.button_firmware}, Battery: {self.ctx.button_battery}%")

        except Exception as e:
            _LOGGER.warning(f"Failed to decode button info: {e}")
            # Use defaults
            self.ctx.button_uuid = ""
            self.ctx.button_name = "Flic 2"
            self.ctx.button_serial = ""
            self.ctx.button_firmware = 0
            self.ctx.button_battery = 0

        self.state = PairingState.FULL_VERIFY_COMPLETE

        # Create credentials and button info
        credentials = PairingCredentials(
            address=self.ctx.button_address.hex() if self.ctx.button_address else "",
            pairing_id=self.ctx.pairing_id,
            pairing_key=self.ctx.pairing_key,
            button_uuid=self.ctx.button_uuid,
            name=self.ctx.button_name,
            serial_number=self.ctx.button_serial,
            firmware_version=self.ctx.button_firmware,
        )

        button_info = ButtonInfo(
            address=credentials.address,
            uuid=self.ctx.button_uuid,
            name=self.ctx.button_name,
            serial_number=self.ctx.button_serial,
            firmware_version=self.ctx.button_firmware,
            battery_level=self.ctx.button_battery,
        )

        if self.on_pairing_complete:
            self.on_pairing_complete(credentials, button_info)

        return True

    async def _handle_quick_verify_response(self, packet: Packet) -> bool:
        """Handle QuickVerifyResponse."""
        if packet.opcode == Opcode.NO_PAIRING_EXISTS:
            self.state = PairingState.FAILED
            self.ctx.error_reason = QuickVerifyFailReason.INVALID_PAIRING_ID
            error_msg = "QuickVerify failed: No pairing exists on button (needs re-pairing)"
            _LOGGER.error(error_msg)
            if self.on_error:
                self.on_error(error_msg)
            return False

        if packet.opcode == Opcode.QUICK_VERIFY_FAIL:
            self.state = PairingState.FAILED
            reason = packet.payload[0] if packet.payload else 0
            self.ctx.error_reason = reason
            error_name = QuickVerifyFailReason(reason).name if reason < 4 else f"UNKNOWN({reason})"
            error_msg = f"QuickVerify failed: {error_name}"
            _LOGGER.error(error_msg)
            if self.on_error:
                self.on_error(error_msg)
            return False

        if packet.opcode != Opcode.QUICK_VERIFY_RESPONSE:
            _LOGGER.warning(f"Unexpected opcode in state {self.state}: {packet.opcode:#x}")
            return False

        # Decode response
        self.ctx.button_random = self.decoder.decode_quick_verify_response(packet.payload)
        self.ctx.conn_id = packet.conn_id

        _LOGGER.debug(f"QuickVerify button_random: {self.ctx.button_random.hex()}")

        # Derive session key
        self.ctx.session_key = derive_quick_verify_session_key(
            self.stored_credentials.pairing_key,
            self.ctx.client_random,
            self.ctx.button_random,
        )
        _LOGGER.debug(f"QuickVerify session key: {self.ctx.session_key.hex()}")

        # Set session key
        self.decoder.set_session_key(self.ctx.session_key)
        self.encoder.set_session_key(self.ctx.session_key)

        if self.on_session_key:
            self.on_session_key(self.ctx.session_key)

        self.state = PairingState.QUICK_VERIFY_COMPLETE

        if self.on_quick_verify_complete:
            self.on_quick_verify_complete(self.ctx.session_key)

        return True

    @property
    def is_complete(self) -> bool:
        """Check if pairing/verification is complete."""
        return self.state in (
            PairingState.FULL_VERIFY_COMPLETE,
            PairingState.QUICK_VERIFY_COMPLETE,
        )

    @property
    def is_failed(self) -> bool:
        """Check if pairing failed."""
        return self.state == PairingState.FAILED

    def get_credentials(self) -> Optional[PairingCredentials]:
        """Get credentials after successful pairing."""
        if self.state != PairingState.FULL_VERIFY_COMPLETE:
            return None

        return PairingCredentials(
            address=self.ctx.button_address.hex() if self.ctx.button_address else "",
            pairing_id=self.ctx.pairing_id,
            pairing_key=self.ctx.pairing_key,
            button_uuid=self.ctx.button_uuid or "",
            name=self.ctx.button_name or "Flic 2",
            serial_number=self.ctx.button_serial or "",
            firmware_version=self.ctx.button_firmware or 0,
        )

    def get_session_key(self) -> Optional[bytes]:
        """Get session key after successful pairing/verification."""
        return self.ctx.session_key
