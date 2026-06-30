"""Main Flic 2 BLE client."""

import asyncio
import logging
from typing import Optional, Callable, List

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

try:
    from bleak_retry_connector import establish_connection
    HAS_RETRY_CONNECTOR = True
except ImportError:
    HAS_RETRY_CONNECTOR = False

from ..const import (
    FLIC2_SERVICE_UUID,
    FLIC2_WRITE_UUID,
    FLIC2_NOTIFY_UUID,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_OPERATION_TIMEOUT,
)
from ..models import (
    ButtonEvent,
    ButtonInfo,
    ConnectionState,
    PairingCredentials,
    SessionState,
)
from ..protocol import PacketEncoder, PacketDecoder, PairingStateMachine, Opcode
from ..exceptions import (
    ConnectionError,
    PairingError,
    NotPairedError,
    TimeoutError,
)
from .scanner import Flic2Scanner


_LOGGER = logging.getLogger(__name__)


class Flic2Client:
    """
    Main client for interacting with Flic 2 buttons.

    Usage:
        client = Flic2Client()

        # Discover buttons
        buttons = await client.scan()

        # Connect
        await client.connect(buttons[0].address)

        # Pair or reconnect
        if not client.has_stored_credentials():
            await client.pair()
        else:
            await client.quick_verify()

        # Handle events
        client.on_button_event = lambda e: print(f"Event: {e}")

        # Listen for events
        await client.listen()
    """

    def __init__(
        self,
        stored_credentials: Optional[PairingCredentials] = None,
    ):
        """
        Initialize Flic 2 client.

        Args:
            stored_credentials: Optional stored credentials for quick verify
        """
        self._bleak_client: Optional[BleakClient] = None
        self._device: Optional[BLEDevice] = None
        self._address: Optional[str] = None

        self._credentials = stored_credentials
        self._session = SessionState()

        self._encoder = PacketEncoder()
        self._decoder = PacketDecoder()
        self._state_machine: Optional[PairingStateMachine] = None

        self._connection_state = ConnectionState.DISCONNECTED
        self._running = False

        # Response handling
        self._response_event = asyncio.Event()
        self._last_response: Optional[bytes] = None

        # Fragmentation
        self._fragment_buffer: bytes = b""
        self._expecting_fragments = False

        # Callbacks
        self.on_button_event: Optional[Callable[[ButtonEvent], None]] = None
        self.on_connection_state_changed: Optional[Callable[[ConnectionState], None]] = None
        self.on_battery_level: Optional[Callable[[int], None]] = None
        self._packet_tasks: set[asyncio.Task[None]] = set()

    @property
    def connection_state(self) -> ConnectionState:
        """Get current connection state."""
        return self._connection_state

    @connection_state.setter
    def connection_state(self, state: ConnectionState):
        """Set connection state and notify."""
        if self._connection_state != state:
            self._connection_state = state
            _LOGGER.debug(f"Connection state: {state.name}")
            if self.on_connection_state_changed:
                self.on_connection_state_changed(state)

    @property
    def is_connected(self) -> bool:
        """Check if connected to button."""
        return (
            self._bleak_client is not None and
            self._bleak_client.is_connected
        )

    @property
    def is_ready(self) -> bool:
        """Check if ready to receive events (paired/verified)."""
        return self.connection_state == ConnectionState.READY

    def has_stored_credentials(self, address: Optional[str] = None) -> bool:
        """Check if we have stored credentials for the given address."""
        if not self._credentials:
            return False
        if address:
            return self._credentials.address.upper() == address.upper()
        return True

    def set_credentials(self, credentials: PairingCredentials):
        """Set stored credentials."""
        self._credentials = credentials

    def get_credentials(self) -> Optional[PairingCredentials]:
        """Get current credentials."""
        return self._credentials

    async def scan(self, timeout: float = 10.0) -> List[BLEDevice]:
        """
        Scan for Flic 2 buttons.

        Args:
            timeout: Scan duration in seconds

        Returns:
            List of discovered Flic 2 devices
        """
        scanner = Flic2Scanner()
        return await scanner.scan(timeout=timeout)

    async def connect(
        self,
        device_or_address: BLEDevice | str,
        timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> bool:
        """
        Connect to a Flic 2 button.

        Args:
            device_or_address: BLEDevice object or Bluetooth address string
            timeout: Connection timeout in seconds

        Returns:
            True if connected successfully
        """
        if self.is_connected:
            await self.disconnect()

        # Accept either BLEDevice (for Home Assistant) or string address
        if isinstance(device_or_address, BLEDevice):
            self._device = device_or_address
            self._address = device_or_address.address
        else:
            self._device = None
            self._address = device_or_address

        self.connection_state = ConnectionState.CONNECTING

        try:
            # Use bleak_retry_connector if available (recommended for HA)
            if HAS_RETRY_CONNECTOR and isinstance(device_or_address, BLEDevice):
                self._bleak_client = await establish_connection(
                    BleakClient,
                    device_or_address,
                    device_or_address.name or self._address,
                    disconnected_callback=self._on_disconnect,
                    max_attempts=3,
                )
            else:
                self._bleak_client = BleakClient(
                    device_or_address,
                    disconnected_callback=self._on_disconnect,
                )
                await asyncio.wait_for(
                    self._bleak_client.connect(),
                    timeout=timeout,
                )

            # Subscribe to notifications
            await self._bleak_client.start_notify(
                FLIC2_NOTIFY_UUID,
                self._on_notification,
            )

            self.connection_state = ConnectionState.CONNECTED
            _LOGGER.info(f"Connected to {self._address}")
            return True

        except asyncio.CancelledError:
            self.connection_state = ConnectionState.DISCONNECTED
            await self.disconnect()
            raise
        except asyncio.TimeoutError:
            self.connection_state = ConnectionState.DISCONNECTED
            raise TimeoutError(f"Connection to {self._address} timed out")
        except BleakError as e:
            self.connection_state = ConnectionState.DISCONNECTED
            raise ConnectionError(f"Failed to connect to {self._address}: {e}")

    async def disconnect(self):
        """Disconnect from button."""
        for task in list(self._packet_tasks):
            task.cancel()
        self._packet_tasks.clear()

        if self._bleak_client:
            try:
                if self._bleak_client.is_connected:
                    await self._bleak_client.disconnect()
            except Exception as e:
                _LOGGER.warning(f"Error during disconnect: {e}")
            finally:
                self._bleak_client = None

        self._session.reset()
        self.connection_state = ConnectionState.DISCONNECTED
        self._running = False

    def _on_disconnect(self, client: BleakClient):
        """Handle disconnection."""
        _LOGGER.info("Disconnected from button %s", self._address)
        self._session.reset()
        self.connection_state = ConnectionState.DISCONNECTED
        self._running = False

    async def _send(self, data: bytes):
        """Send data to button."""
        if not self._bleak_client or not self._bleak_client.is_connected:
            raise ConnectionError("Not connected")

        _LOGGER.debug(f"TX ({len(data)} bytes): {data.hex()}")

        # Let Bleak/BLE layer handle MTU negotiation and fragmentation
        await self._bleak_client.write_gatt_char(
            FLIC2_WRITE_UUID,
            data,
            response=False,
        )

    def _on_notification(self, sender, data: bytes):
        """Handle incoming notification."""
        _LOGGER.debug(f"RX: {data.hex()}")

        # Note: BLE fragmentation is handled by the OS/Bleak layer
        # We receive complete notifications even if they exceed MTU

        # Store response for synchronous waiting
        self._last_response = data
        self._response_event.set()

        # Process packet asynchronously
        task = asyncio.create_task(self._process_packet(data))
        self._packet_tasks.add(task)
        task.add_done_callback(self._packet_tasks.discard)

    async def _process_packet(self, data: bytes):
        """Process received packet."""
        try:
            # During pairing, delegate to state machine
            if self._state_machine and not self._state_machine.is_complete:
                await self._state_machine.handle_packet(data)
                return

            # After pairing, handle events
            packet = self._decoder.decode(data)

            if packet.opcode in (Opcode.BUTTON_EVENT_SINGLE, Opcode.BUTTON_EVENT_NOTIFICATION):
                events = self._decoder.decode_button_event(packet.payload)
                for event in events:
                    _LOGGER.debug(f"Button event: {event}")
                    if self.on_button_event:
                        self.on_button_event(event)

            elif packet.opcode == Opcode.PING_RESPONSE:
                _LOGGER.debug("Ping response received")

            # Note: Battery status is included in init_button_events response payload,
            # not as a separate opcode. Could be extracted from there if needed.

        except Exception as e:
            import traceback
            _LOGGER.error(f"Error processing packet: {e}")
            _LOGGER.error(traceback.format_exc())

    async def _wait_for_response(
        self,
        timeout: float = DEFAULT_OPERATION_TIMEOUT,
    ) -> bytes:
        """Wait for a response packet."""
        self._response_event.clear()
        try:
            await asyncio.wait_for(
                self._response_event.wait(),
                timeout=timeout,
            )
            return self._last_response
        except asyncio.TimeoutError:
            raise TimeoutError("Response timeout")

    async def pair(
        self,
        timeout: float = 30.0,
    ) -> PairingCredentials:
        """
        Perform full verify pairing.

        Args:
            timeout: Pairing timeout in seconds

        Returns:
            Pairing credentials

        Raises:
            PairingError: If pairing fails
        """
        if not self.is_connected:
            raise ConnectionError("Not connected")

        self.connection_state = ConnectionState.PAIRING
        _LOGGER.info("Starting full verify pairing...")

        # Create state machine
        self._state_machine = PairingStateMachine(
            send_func=self._send,
            stored_credentials=None,
        )

        # Track completion
        pairing_complete = asyncio.Event()
        pairing_error: Optional[str] = None
        result_credentials: Optional[PairingCredentials] = None

        def on_complete(creds: PairingCredentials, info: ButtonInfo):
            nonlocal result_credentials
            result_credentials = creds
            pairing_complete.set()

        def on_error(msg: str):
            nonlocal pairing_error
            pairing_error = msg
            pairing_complete.set()

        def on_session_key(key: bytes):
            self._session.session_key = key
            self._decoder.set_session_key(key)
            self._encoder.set_session_key(key)

        self._state_machine.on_pairing_complete = on_complete
        self._state_machine.on_error = on_error
        self._state_machine.on_session_key = on_session_key

        # Start pairing
        await self._state_machine.start_full_verify()

        # Wait for completion
        try:
            await asyncio.wait_for(pairing_complete.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.connection_state = ConnectionState.CONNECTED
            raise TimeoutError("Pairing timed out")

        if pairing_error:
            self.connection_state = ConnectionState.CONNECTED
            raise PairingError(pairing_error)

        if result_credentials:
            self._credentials = result_credentials
            self._session.is_paired = True
            self.connection_state = ConnectionState.READY
            _LOGGER.info("Pairing successful!")
            return result_credentials

        raise PairingError("Pairing failed for unknown reason")

    async def quick_verify(
        self,
        timeout: float = 10.0,
    ) -> bool:
        """
        Perform quick verify reconnection.

        Args:
            timeout: Verification timeout in seconds

        Returns:
            True if verification successful

        Raises:
            NotPairedError: If no stored credentials
            PairingError: If verification fails
        """
        if not self.is_connected:
            raise ConnectionError("Not connected")

        if not self._credentials:
            raise NotPairedError("No stored credentials for quick verify")

        self.connection_state = ConnectionState.QUICK_VERIFYING
        _LOGGER.info("Starting quick verify...")

        # Create state machine
        self._state_machine = PairingStateMachine(
            send_func=self._send,
            stored_credentials=self._credentials,
        )

        # Track completion
        verify_complete = asyncio.Event()
        verify_error: Optional[str] = None

        def on_complete(key: bytes):
            verify_complete.set()

        def on_error(msg: str):
            nonlocal verify_error
            verify_error = msg
            verify_complete.set()

        def on_session_key(key: bytes):
            self._session.session_key = key
            self._decoder.set_session_key(key)
            self._encoder.set_session_key(key)

        self._state_machine.on_quick_verify_complete = on_complete
        self._state_machine.on_error = on_error
        self._state_machine.on_session_key = on_session_key

        # Start quick verify
        await self._state_machine.start_quick_verify()

        # Wait for completion
        try:
            await asyncio.wait_for(verify_complete.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self.connection_state = ConnectionState.CONNECTED
            raise TimeoutError("Quick verify timed out")

        if verify_error:
            self.connection_state = ConnectionState.CONNECTED
            raise PairingError(verify_error)

        # Transfer state from state machine to session
        self._session.conn_id = self._state_machine.ctx.conn_id
        self._session.is_paired = True
        self.connection_state = ConnectionState.READY
        _LOGGER.info(f"Quick verify successful! (conn_id={self._session.conn_id})")
        return True

    async def init_button_events(self, timeout: float = 10.0) -> bool:
        """
        Initialize button event reception.

        Must be called after pairing or quick verify to start receiving events.

        Returns:
            True if initialization successful
        """
        if not self.is_connected or not self._session.session_key:
            raise NotPairedError("Not connected or no session key")

        _LOGGER.info("Initializing button events...")

        # Build InitButtonEventsLight payload
        event_count = 0
        boot_id = 0
        auto_disconnect_time = 511  # Max value (disabled)
        max_queued_packets = 31
        max_queued_packets_age = 0xFFFFF
        enable_hid = 0

        # Pack bit fields
        bitfield_val = (
            auto_disconnect_time |
            (max_queued_packets << 9) |
            (max_queued_packets_age << 14) |
            (enable_hid << 34)
        )
        bitfield_bytes = bitfield_val.to_bytes(5, 'little')

        payload = (
            event_count.to_bytes(4, 'little') +
            boot_id.to_bytes(4, 'little') +
            bitfield_bytes
        )

        # Build packet with opcode 0x17 (INIT_BUTTON_EVENTS)
        packet_body = bytes([0x17]) + payload

        # Sign packet
        from ..crypto import ChaskeyLTS
        chaskey = ChaskeyLTS(self._session.session_key)
        signature = chaskey.mac_with_dir_and_counter(packet_body, 1, self._session.tx_counter)
        self._session.tx_counter += 1

        # Build final packet with conn_id header
        packet = bytes([self._session.conn_id & 0x1F]) + packet_body + signature

        _LOGGER.debug(f"TX init_button_events ({len(packet)} bytes): {packet.hex()}")
        await self._send(packet)

        # Wait for response
        try:
            response = await self._wait_for_response(timeout=timeout)
        except TimeoutError:
            _LOGGER.warning("Init button events timed out")
            return False

        opcode = response[1] if len(response) > 1 else 0
        _LOGGER.debug(f"Init button events response opcode: {opcode:#04x}")

        if opcode == 0x09:  # DISCONNECTED_LINK
            reason = response[2] if len(response) > 2 else 0
            reasons = {0: "PING_TIMEOUT", 1: "INVALID_SIGNATURE", 2: "NEW_CONNECTION", 3: "BY_USER"}
            _LOGGER.error(f"Button disconnected during init: {reasons.get(reason, reason)}")
            return False

        if opcode in (0x0A, 0x0B):  # INIT_BUTTON_EVENTS_RESPONSE or NO_BOOT
            _LOGGER.info("Button events initialized successfully!")

            # Extract battery level from response payload
            # Response format: header(1) + opcode(1) + payload(13+) + signature(5)
            # Payload: boot_id(4) + event_count(4) + timestamp_hi(4) + battery(1)
            from ..const import SIGNATURE_LENGTH
            if len(response) > 2 + SIGNATURE_LENGTH:
                # Strip header, opcode, and signature
                payload = response[2:-SIGNATURE_LENGTH]
                _LOGGER.debug("Init response payload (%d bytes): %s", len(payload), payload.hex())
                try:
                    boot_id, event_count, timestamp_hi, battery_level = (
                        self._decoder.decode_init_button_events_response(payload)
                    )
                    _LOGGER.debug(
                        "Init response: boot_id=%d, events=%d, battery=%d%%",
                        boot_id, event_count, battery_level
                    )
                    # Report battery level
                    if self.on_battery_level and battery_level > 0:
                        self.on_battery_level(battery_level)
                except Exception as err:
                    _LOGGER.warning("Failed to decode init response payload: %s", err)

            return True

        _LOGGER.warning(f"Unexpected init response opcode: {opcode:#04x}")
        return False

    async def listen(self):
        """
        Listen for button events.

        This runs until disconnect or stop() is called.
        """
        if not self.is_ready:
            raise NotPairedError("Not paired/verified - cannot listen for events")

        _LOGGER.info("Listening for button events...")
        self._running = True

        while self._running and self.is_connected:
            await asyncio.sleep(0.1)

        _LOGGER.info("Stopped listening")

    def stop(self):
        """Stop listening for events."""
        self._running = False

    async def ping(self) -> bool:
        """
        Send ping and wait for response.

        Returns:
            True if ping successful
        """
        if not self.is_connected:
            return False

        packet = self._encoder.encode_ping(conn_id=self._session.conn_id)
        await self._send(packet)

        try:
            response = await self._wait_for_response(timeout=2.0)
            opcode = response[1] if len(response) > 1 else 0

            if opcode == Opcode.PING_RESPONSE:
                return True

            if opcode == Opcode.DISCONNECTED_LINK:
                reason = response[2] if len(response) > 2 else 0
                reasons = {
                    0: "PING_TIMEOUT",
                    1: "INVALID_SIGNATURE",
                    2: "NEW_CONNECTION",
                    3: "BY_USER",
                }
                _LOGGER.warning(
                    "Ping observed disconnect from button: %s",
                    reasons.get(reason, reason),
                )
                return False

            # Non-ping packets can arrive while listening; keep connection unless link dropped.
            return self.is_connected
        except TimeoutError:
            return False

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
