"""Base entity for Flic 2 BLE integration."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER, SIGNAL_CONNECTION_CHANGED
from .coordinator import Flic2Coordinator


class Flic2Entity(Entity):
    """Base class for Flic 2 entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: Flic2Coordinator) -> None:
        """Initialize the entity."""
        self.coordinator = coordinator
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            identifiers={(DOMAIN, coordinator.button_uuid)},
            manufacturer=MANUFACTURER,
            name=coordinator.device_name,
            model="Flic 2",
            serial_number=coordinator.serial_number,
            sw_version=str(coordinator.firmware_version) if coordinator.firmware_version else None,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator connection changes."""
        await super().async_added_to_hass()

        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_CONNECTION_CHANGED}_{self.coordinator.address}",
                self._handle_connection_changed,
            )
        )

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        return self.coordinator.available

    @callback
    def _handle_connection_changed(self, available: bool) -> None:
        """Refresh state when connection availability changes."""
        self.async_write_ha_state()
