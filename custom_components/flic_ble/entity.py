"""Base entity for Flic 2 BLE integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER
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

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        return self.coordinator.available
