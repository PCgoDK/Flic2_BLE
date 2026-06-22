"""Diagnostics support for Flic 2 BLE integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_PAIRING_ID, CONF_PAIRING_KEY
from .coordinator import FlicConfigEntry

# Keys to redact from diagnostics
TO_REDACT = {CONF_PAIRING_ID, CONF_PAIRING_KEY}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FlicConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "coordinator": coordinator.get_diagnostics_data(),
    }
