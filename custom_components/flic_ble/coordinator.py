"""Coordinator for Flic 2 BLE integration."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components import bluetooth
from homeassistant.const import CONF_DEVICE_ID, CONF_TYPE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    CONF_ADDRESS,
    CONF_BUTTON_UUID,
    CONF_FIRMWARE_VERSION,
    CONF_NAME,
    CONF_PAIRING_ID,
    CONF_PAIRING_KEY,
    CONF_SERIAL_NUMBER,
    CONNECTION_TIMEOUT,
    DOMAIN,
    EVENT_DOUBLE_PRESS,
    EVENT_HOLD,
    EVENT_SINGLE_PRESS,
    KEEPALIVE_INTERVAL,
    QUICK_VERIFY_TIMEOUT,
    RECONNECT_INTERVAL,
    RECONNECT_MAX_INTERVAL,
    SIGNAL_BATTERY_UPDATE,
    SIGNAL_BUTTON_EVENT,
    SIGNAL_CONNECTION_CHANGED,
)
from .flic2 import (
    ButtonEvent,
    ButtonEventType,
    ConnectionState,
    Flic2Client,
    PairingCredentials,
    PairingError,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


type FlicConfigEntry = ConfigEntry[Flic2Coordinator]


class Flic2Coordinator:
    """Coordinator to manage connection and events for a Flic 2 button."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.config_entry = config_entry

        # Device information from config entry
        self.address: str = config_entry.data[CONF_ADDRESS]
        self.device_name: str = config_entry.data.get(CONF_NAME) or config_entry.title
        self.button_uuid: str = config_entry.data[CONF_BUTTON_UUID]
        self.serial_number: str = config_entry.data.get(CONF_SERIAL_NUMBER, "")
        self.firmware_version: int = config_entry.data.get(CONF_FIRMWARE_VERSION, 0)

        # Restore credentials from config entry
        self._credentials = self._restore_credentials()

        # Client and state
        self._client = Flic2Client(stored_credentials=self._credentials)
        self._available = False
        self._battery_level: int | None = None
        self._running = False
        self._reconnect_task: asyncio.Task[None] | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._reconnect_attempt = 0

        # Set up callbacks
        self._client.on_button_event = self._handle_button_event
        self._client.on_battery_level = self._handle_battery_update
        self._client.on_connection_state_changed = self._handle_connection_change

        # Event callbacks for entities
        self._event_callbacks: list[callback] = []

    def _restore_credentials(self) -> PairingCredentials:
        """Restore pairing credentials from config entry data."""
        data = self.config_entry.data
        return PairingCredentials(
            address=data[CONF_ADDRESS],
            pairing_id=bytes.fromhex(data[CONF_PAIRING_ID]),
            pairing_key=bytes.fromhex(data[CONF_PAIRING_KEY]),
            button_uuid=data[CONF_BUTTON_UUID],
            name=data.get(CONF_NAME, ""),
            serial_number=data.get(CONF_SERIAL_NUMBER, ""),
            firmware_version=data.get(CONF_FIRMWARE_VERSION, 0),
        )

    @property
    def available(self) -> bool:
        """Return whether the device is available."""
        return self._available

    @property
    def battery_level(self) -> int | None:
        """Return the battery level."""
        return self._battery_level

    async def async_start(self) -> None:
        """Start the coordinator."""
        _LOGGER.debug("Starting Flic 2 coordinator for %s", self.address)
        self._running = True

        # Register for Bluetooth unavailability tracking
        self.config_entry.async_on_unload(
            bluetooth.async_track_unavailable(
                self.hass,
                self._handle_bluetooth_unavailable,
                self.address,
                connectable=True,
            )
        )

        # Start initial connection
        await self._async_connect()

    async def async_stop(self) -> None:
        """Stop the coordinator."""
        _LOGGER.debug("Stopping Flic 2 coordinator for %s", self.address)
        self._running = False

        # Cancel reconnect task
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Cancel listen task
        if self._listen_task and not self._listen_task.done():
            self._client.stop()
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass

        # Cancel keepalive task
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass

        # Disconnect client
        await self._client.disconnect()
        self._available = False

    async def _async_connect(self) -> None:
        """Connect to the Flic 2 button."""
        if not self._running:
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if not ble_device:
            _LOGGER.debug("Device %s not found, scheduling reconnect", self.address)
            self._schedule_reconnect()
            return

        try:
            _LOGGER.debug("Connecting to %s", self.address)
            await self._client.connect(ble_device, timeout=CONNECTION_TIMEOUT)

            _LOGGER.debug("Performing quick verify for %s", self.address)
            await self._client.quick_verify(timeout=QUICK_VERIFY_TIMEOUT)

            _LOGGER.debug("Initializing button events for %s", self.address)
            if not await self._client.init_button_events():
                _LOGGER.warning("Failed to initialize button events for %s", self.address)
                raise Exception("Failed to initialize button events")

            self._available = True
            self._reconnect_attempt = 0
            _LOGGER.info("Connected and verified with %s", self.address)

            # Start listening for events in background
            self._listen_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_listen(),
                f"flic_ble_listen_{self.address}",
            )

            # Start keepalive loop in background to avoid idle disconnects.
            self._keepalive_task = self.config_entry.async_create_background_task(
                self.hass,
                self._async_keepalive(),
                f"flic_ble_keepalive_{self.address}",
            )

        except PairingError as err:
            error_msg = str(err)
            _LOGGER.warning("Pairing error for %s: %s", self.address, error_msg)
            self._available = False
            await self._client.disconnect()

            # If the button doesn't have our pairing, trigger re-auth flow
            if "no pairing exists" in error_msg.lower():
                raise ConfigEntryAuthFailed(
                    "Button pairing was lost. Please re-pair the device."
                ) from err

            self._schedule_reconnect()

        except Exception as err:
            _LOGGER.warning(
                "Failed to connect to %s: %s, scheduling reconnect",
                self.address,
                err,
            )
            self._available = False
            await self._client.disconnect()
            self._schedule_reconnect()

    async def _async_listen(self) -> None:
        """Listen for button events."""
        try:
            await self._client.listen()
        except Exception as err:
            _LOGGER.warning("Listen task ended for %s: %s", self.address, err)
        finally:
            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()

            if self._running:
                _LOGGER.info("Connection lost to %s, scheduling reconnect", self.address)
                self._available = False
                self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt."""
        if not self._running:
            return

        if self._reconnect_task and not self._reconnect_task.done():
            return  # Already scheduled

        delay = min(
            RECONNECT_INTERVAL * (2 ** self._reconnect_attempt),
            RECONNECT_MAX_INTERVAL,
        )
        self._reconnect_attempt += 1

        _LOGGER.debug(
            "Scheduling reconnect for %s in %ss (attempt %s)",
            self.address,
            delay,
            self._reconnect_attempt,
        )

        self._reconnect_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_reconnect_after_delay(delay),
            f"flic_ble_reconnect_{self.address}",
        )

    async def _async_reconnect_after_delay(self, delay: int) -> None:
        """Wait and then attempt reconnection."""
        await asyncio.sleep(delay)

        # Clear the task reference before connecting so a failed reconnect
        # can schedule a new retry from inside _async_connect.
        if self._reconnect_task is asyncio.current_task():
            self._reconnect_task = None

        if self._running:
            await self._async_connect()

    async def _async_keepalive(self) -> None:
        """Send periodic ping to keep the BLE link healthy."""
        try:
            while self._running and self._client.is_ready and self._client.is_connected:
                await asyncio.sleep(KEEPALIVE_INTERVAL)

                if not self._running or not self._client.is_connected:
                    break

                try:
                    if await self._client.ping():
                        _LOGGER.debug("Keepalive ping succeeded for %s", self.address)
                        continue
                except Exception as err:
                    _LOGGER.warning("Keepalive ping error for %s: %s", self.address, err)

                _LOGGER.warning("Keepalive ping failed for %s", self.address)
                await self._client.disconnect()
                self._available = False
                self._schedule_reconnect()
                return
        except asyncio.CancelledError:
            return

    @callback
    def _handle_bluetooth_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle Bluetooth device becoming unavailable."""
        _LOGGER.debug("Bluetooth device %s became unavailable", self.address)
        # Connection will be handled by the listen task ending

    def _handle_button_event(self, event: ButtonEvent) -> None:
        """Handle button event from client."""
        _LOGGER.debug("Button event from %s: %s", self.address, event)

        # Map button event type to our event type string
        # Note: Only CLICK should map to single_press. SINGLE_CLICK (type 3) is sent
        # right before HOLD events as an internal state transition and should be ignored.
        event_type_map = {
            ButtonEventType.CLICK: EVENT_SINGLE_PRESS,
            ButtonEventType.DOUBLE_CLICK: EVENT_DOUBLE_PRESS,
            ButtonEventType.HOLD: EVENT_HOLD,
        }

        event_type = event_type_map.get(event.event_type)
        if not event_type:
            _LOGGER.debug("Ignoring unmapped event type: %s", event.event_type)
            return  # Ignore events we don't map (e.g., UP, DOWN)

        _LOGGER.debug("Mapped event %s -> %s", event.event_type, event_type)

        # Dispatch event to entities
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_BUTTON_EVENT}_{self.address}",
            event,
        )

        # Fire event on event bus for device triggers
        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(
            identifiers={(DOMAIN, self.button_uuid)}
        )
        if device:
            _LOGGER.info("Firing %s event for device %s", event_type, device.id)
            self.hass.bus.async_fire(
                f"{DOMAIN}_event",
                {
                    CONF_DEVICE_ID: device.id,
                    CONF_TYPE: event_type,
                },
            )
        else:
            _LOGGER.warning("Device not found for button_uuid: %s", self.button_uuid)

        # Also notify direct subscribers
        for cb in self._event_callbacks:
            try:
                cb(event)
            except Exception:
                _LOGGER.exception("Error in event callback")

    def _handle_battery_update(self, level: int) -> None:
        """Handle battery level update from client."""
        _LOGGER.debug("Battery level for %s: %d%%", self.address, level)
        self._battery_level = level

        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_BATTERY_UPDATE}_{self.address}",
            level,
        )

    def _handle_connection_change(self, state: ConnectionState) -> None:
        """Handle connection state change from client."""
        _LOGGER.debug("Connection state for %s: %s", self.address, state.name)

        was_available = self._available
        self._available = state == ConnectionState.READY

        if was_available != self._available:
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_CONNECTION_CHANGED}_{self.address}",
                self._available,
            )

    @callback
    def async_subscribe_events(
        self, callback_func: callback
    ) -> callback:
        """Subscribe to button events. Returns unsubscribe callable."""
        self._event_callbacks.append(callback_func)

        @callback
        def unsubscribe() -> None:
            self._event_callbacks.remove(callback_func)

        return unsubscribe

    def get_diagnostics_data(self) -> dict[str, Any]:
        """Return diagnostics data."""
        return {
            "address": self.address,
            "device_name": self.device_name,
            "button_uuid": self.button_uuid,
            "serial_number": self.serial_number,
            "firmware_version": self.firmware_version,
            "available": self._available,
            "battery_level": self._battery_level,
            "connection_state": self._client.connection_state.name,
        }
