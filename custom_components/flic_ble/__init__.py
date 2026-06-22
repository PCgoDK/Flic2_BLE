"""The Flic 2 BLE integration."""

from __future__ import annotations

import logging

from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_ADDRESS,
    CONF_BUTTON_UUID,
    CONF_FIRMWARE_VERSION,
    CONF_NAME,
    CONF_SERIAL_NUMBER,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import Flic2Coordinator, FlicConfigEntry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.EVENT, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: FlicConfigEntry) -> bool:
    """Set up Flic 2 BLE from a config entry."""
    address = entry.data[CONF_ADDRESS]

    # Verify device is discoverable
    ble_device = async_ble_device_from_address(hass, address, connectable=True)
    if not ble_device:
        _LOGGER.warning(
            "Device %s not found during setup, will retry when available", address
        )
        # Still continue setup - coordinator will handle reconnection

    # Create coordinator
    coordinator = Flic2Coordinator(hass, entry)
    entry.runtime_data = coordinator

    # Register device in device registry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        connections={(dr.CONNECTION_BLUETOOTH, address)},
        identifiers={(DOMAIN, entry.data[CONF_BUTTON_UUID])},
        manufacturer=MANUFACTURER,
        name=entry.data.get(CONF_NAME) or entry.title,
        model="Flic 2",
        serial_number=entry.data.get(CONF_SERIAL_NUMBER),
        sw_version=str(entry.data.get(CONF_FIRMWARE_VERSION, "")),
    )

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start coordinator
    await coordinator.async_start()

    # Register cleanup on unload
    entry.async_on_unload(coordinator.async_stop)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: FlicConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
