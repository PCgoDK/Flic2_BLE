"""Coordinator for Flic 2 BLE integration."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from bleak.exc import BleakError
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

    _WATCHDOG_TIMEOUT = 60
    _WATCHDOG_CHECK_INTERVAL = 10
    _SCANNER_LOOP_INTERVAL = 30
    _ACTIVE_SCAN_TIMEOUT = 8

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
        self._watchdog_task: asyncio.Task[None] | None = None
        self._scanner_task: asyncio.Task[None] | None = None
        self._reconnect_attempt = 0
        self._connect_lock = asyncio.Lock()
        self._last_event_monotonic = time.monotonic()

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
        if self._running:
            return

        self._running = True
        self._last_event_monotonic = time.monotonic()

        # Register for Bluetooth unavailability tracking
        self.config_entry.async_on_unload(
            bluetooth.async_track_unavailable(
                self.hass,
                self._handle_bluetooth_unavailable,
                self.address,
                connectable=True,
            )
        )

        self._watchdog_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_watchdog_loop(),
            f"flic_ble_watchdog_{self.address}",
        )
        self._scanner_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_scanner_loop(),
            f"flic_ble_scanner_{self.address}",
        )

        # Start initial connection
        await self._async_connect(raise_auth_failed=True)

    async def async_stop(self) -> None:
        """Stop the coordinator."""
        _LOGGER.debug("Stopping Flic 2 coordinator for %s", self.address)
        if not self._running:
            return

        self._running = False
        self._client.stop()

        await self._async_cancel_task(self._reconnect_task)
        await self._async_cancel_task(self._listen_task)
        await self._async_cancel_task(self._keepalive_task)
        await self._async_cancel_task(self._watchdog_task)
        await self._async_cancel_task(self._scanner_task)

        self._reconnect_task = None
        self._listen_task = None
        self._keepalive_task = None
        self._watchdog_task = None
        self._scanner_task = None

        # Disconnect client
        await self._client.disconnect()
        self._available = False
        async_dispatcher_send(
            self.hass,
            f"{SIGNAL_CONNECTION_CHANGED}_{self.address}",
            self._available,
        )

    async def _async_cancel_task(self, task: asyncio.Task[None] | None) -> None:
        """Cancel a background task and wait for it to finish."""
        if not task or task.done():
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            _LOGGER.debug("Background task cancellation raised", exc_info=True)

    async def _async_connect(self, *, raise_auth_failed: bool = False) -> None:
        """Connect to the Flic 2 button."""
        if not self._running:
            return

        async with self._connect_lock:
            if not self._running:
                return

            if self._client.is_connected and self._client.is_ready:
                _LOGGER.debug("Connection already ready for %s", self.address)
                self._available = True
                return

            await self._async_cancel_task(self._listen_task)
            self._listen_task = None
            await self._async_cancel_task(self._keepalive_task)
            self._keepalive_task = None

            ble_device = bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
            if not ble_device:
                _LOGGER.debug("Device %s not found, scheduling reconnect", self.address)
                self._schedule_reconnect(reason="device_not_found")
                return

            try:
                _LOGGER.debug("Connecting to %s", self.address)
                await self._client.connect(ble_device, timeout=CONNECTION_TIMEOUT)

                _LOGGER.debug("Performing quick verify for %s", self.address)
                await self._client.quick_verify(timeout=QUICK_VERIFY_TIMEOUT)

                _LOGGER.debug("Initializing button events for %s", self.address)
                if not await self._client.init_button_events():
                    _LOGGER.warning("Failed to initialize button events for %s", self.address)
                    raise RuntimeError("Failed to initialize button events")

                self._available = True
                self._last_event_monotonic = time.monotonic()
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

            except asyncio.CancelledError:
                _LOGGER.debug("Connect flow cancelled for %s", self.address)
                raise
            except PairingError as err:
                error_msg = str(err)
                _LOGGER.warning("Pairing error for %s: %s", self.address, error_msg)
                self._available = False
                await self._client.disconnect()

                # If the button doesn't have our pairing, trigger re-auth flow
                if raise_auth_failed and "no pairing exists" in error_msg.lower():
                    raise ConfigEntryAuthFailed(
                        "Button pairing was lost. Please re-pair the device."
                    ) from err

                self._schedule_reconnect(reason="pairing_error")
            except (asyncio.TimeoutError, BleakError, Exception) as err:
                _LOGGER.warning(
                    "Failed to connect to %s: %s, scheduling reconnect",
                    self.address,
                    err,
                )
                self._available = False
                await self._client.disconnect()
                self._schedule_reconnect(reason="connect_error")

    async def _async_listen(self) -> None:
        """Listen for button events."""
        try:
            await self._client.listen()
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning("Listen task ended for %s: %s", self.address, err)
        finally:
            if self._keepalive_task and not self._keepalive_task.done():
                self._keepalive_task.cancel()

            if self._running:
                _LOGGER.info("Connection lost to %s, scheduling reconnect", self.address)
                self._available = False
                self._schedule_reconnect(reason="listen_ended")

    def _schedule_reconnect(self, *, reason: str, immediate: bool = False) -> None:
        """Schedule a reconnection attempt."""
        if not self._running:
            return

        if self._reconnect_task and not self._reconnect_task.done():
            return  # Already scheduled

        if immediate:
            delay = 0
            attempt = self._reconnect_attempt
        else:
            delay = min(
                RECONNECT_INTERVAL * (2 ** self._reconnect_attempt),
                RECONNECT_MAX_INTERVAL,
            )
            self._reconnect_attempt += 1
            attempt = self._reconnect_attempt

        _LOGGER.debug(
            "Scheduling reconnect for %s in %ss (attempt %s, reason=%s)",
            self.address,
            delay,
            attempt,
            reason,
        )

        self._reconnect_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_reconnect_after_delay(delay),
            f"flic_ble_reconnect_{self.address}",
        )

    async def _async_reconnect_after_delay(self, delay: int) -> None:
        """Wait and then attempt reconnection."""
        try:
            if delay > 0:
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

        # Clear the task reference before connecting so a failed reconnect
        # can schedule a new retry from inside _async_connect.
        if self._reconnect_task is asyncio.current_task():
            self._reconnect_task = None

        if self._running:
            try:
                await self._async_connect(raise_auth_failed=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.debug("Reconnect attempt failed", exc_info=True)
                self._schedule_reconnect(reason="reconnect_exception")

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
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    _LOGGER.warning("Keepalive ping error for %s: %s", self.address, err)

                _LOGGER.warning("Keepalive ping failed for %s", self.address)
                await self._client.disconnect()
                self._available = False
                self._schedule_reconnect(reason="keepalive_failed", immediate=True)
                return
        except asyncio.CancelledError:
            return

    async def _async_watchdog_loop(self) -> None:
        """Watch for stale event streams and self-heal the BLE session."""
        try:
            while self._running:
                await asyncio.sleep(self._WATCHDOG_CHECK_INTERVAL)

                if not self._running:
                    return

                if not self._available or not self._client.is_connected:
                    continue

                idle_for = time.monotonic() - self._last_event_monotonic
                if idle_for < self._WATCHDOG_TIMEOUT:
                    continue

                _LOGGER.warning(
                    "Watchdog triggered for %s (no events for %.1fs), restarting BLE session",
                    self.address,
                    idle_for,
                )
                self._last_event_monotonic = time.monotonic()
                await self._async_restart_connection(reason="watchdog_timeout")
        except asyncio.CancelledError:
            return

    async def _async_scanner_loop(self) -> None:
        """Continuously try to rediscover the button while disconnected."""
        try:
            while self._running:
                await asyncio.sleep(self._SCANNER_LOOP_INTERVAL)

                if not self._running:
                    return

                if self._client.is_connected:
                    continue

                # First use Home Assistant's scanner cache.
                ble_device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if ble_device:
                    _LOGGER.debug(
                        "Device %s visible via HA Bluetooth cache, requesting reconnect",
                        self.address,
                    )
                    self._schedule_reconnect(reason="scanner_cache_hit", immediate=True)
                    continue

                # Fallback active scan: callback registration happens per scan run.
                try:
                    devices = await self._client.scan(timeout=self._ACTIVE_SCAN_TIMEOUT)
                except asyncio.CancelledError:
                    raise
                except Exception as err:
                    _LOGGER.debug("Fallback BLE scan failed for %s: %s", self.address, err)
                    continue

                if any(d.address.upper() == self.address.upper() for d in devices):
                    _LOGGER.debug(
                        "Device %s found during fallback active scan, requesting reconnect",
                        self.address,
                    )
                    self._schedule_reconnect(reason="scanner_active_hit", immediate=True)
        except asyncio.CancelledError:
            return

    async def _async_restart_connection(self, *, reason: str) -> None:
        """Disconnect and request immediate reconnect."""
        self._client.stop()
        await self._client.disconnect()
        self._available = False
        self._schedule_reconnect(reason=reason, immediate=True)

    @callback
    def _handle_bluetooth_unavailable(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Handle Bluetooth device becoming unavailable."""
        _LOGGER.warning("Bluetooth device %s became unavailable", self.address)
        self._available = False
        self._schedule_reconnect(reason="bluetooth_unavailable")

    def _handle_button_event(self, event: ButtonEvent) -> None:
        """Handle button event from client."""
        self._last_event_monotonic = time.monotonic()
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
        if self._available:
            self._last_event_monotonic = time.monotonic()

        if was_available != self._available:
            async_dispatcher_send(
                self.hass,
                f"{SIGNAL_CONNECTION_CHANGED}_{self.address}",
                self._available,
            )

        if self._running and state == ConnectionState.DISCONNECTED:
            _LOGGER.info("Disconnected from %s, requesting reconnect", self.address)
            self._schedule_reconnect(reason="state_disconnected")

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
