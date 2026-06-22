"""Sensor entity for Flic 2 BLE integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SIGNAL_BATTERY_UPDATE
from .coordinator import Flic2Coordinator, FlicConfigEntry
from .entity import Flic2Entity

BATTERY_DESCRIPTION = SensorEntityDescription(
    key="battery",
    translation_key="battery",
    device_class=SensorDeviceClass.BATTERY,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=PERCENTAGE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FlicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Flic 2 battery sensor from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([Flic2BatterySensor(coordinator)])


class Flic2BatterySensor(Flic2Entity, SensorEntity):
    """Representation of a Flic 2 battery sensor."""

    entity_description = BATTERY_DESCRIPTION

    def __init__(self, coordinator: Flic2Coordinator) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.button_uuid}_battery"

    @property
    def native_value(self) -> int | None:
        """Return the battery level."""
        return self.coordinator.battery_level

    async def async_added_to_hass(self) -> None:
        """Subscribe to battery updates when entity is added."""
        await super().async_added_to_hass()

        # Subscribe to battery updates
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_BATTERY_UPDATE}_{self.coordinator.address}",
                self._handle_battery_update,
            )
        )

    @callback
    def _handle_battery_update(self, level: int) -> None:
        """Handle battery level update."""
        self.async_write_ha_state()
