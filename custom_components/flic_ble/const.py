"""Constants for Flic 2 BLE integration."""

from typing import Final

DOMAIN: Final = "flic_ble"
MANUFACTURER: Final = "Shortcut Labs"

# Flic 2 Bluetooth service UUID
FLIC2_SERVICE_UUID: Final = "00420000-8f59-4420-870d-84f3b617e493"

# Config entry data keys
CONF_ADDRESS: Final = "address"
CONF_NAME: Final = "name"
CONF_PAIRING_ID: Final = "pairing_id"
CONF_PAIRING_KEY: Final = "pairing_key"
CONF_BUTTON_UUID: Final = "button_uuid"
CONF_SERIAL_NUMBER: Final = "serial_number"
CONF_FIRMWARE_VERSION: Final = "firmware_version"

# Event types for button presses
EVENT_SINGLE_PRESS: Final = "single_press"
EVENT_DOUBLE_PRESS: Final = "double_press"
EVENT_HOLD: Final = "hold"
EVENT_TYPES: Final[list[str]] = [EVENT_SINGLE_PRESS, EVENT_DOUBLE_PRESS, EVENT_HOLD]

# Timeouts (seconds)
CONNECTION_TIMEOUT: Final = 15
PAIRING_TIMEOUT: Final = 30
QUICK_VERIFY_TIMEOUT: Final = 10
RECONNECT_INTERVAL: Final = 30
RECONNECT_MAX_INTERVAL: Final = 300
KEEPALIVE_INTERVAL: Final = 25

# Dispatcher signals
SIGNAL_BUTTON_EVENT: Final = f"{DOMAIN}_button_event"
SIGNAL_BATTERY_UPDATE: Final = f"{DOMAIN}_battery_update"
SIGNAL_CONNECTION_CHANGED: Final = f"{DOMAIN}_connection_changed"
