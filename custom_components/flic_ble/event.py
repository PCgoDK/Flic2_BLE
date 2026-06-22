"""Event entity for Flic 2 BLE integration."""

from __future__ import annotations

from homeassistant.components.event import (
    EventDeviceClass,
    EventEntity,
    EventEntityDescription,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    EVENT_DOUBLE_PRESS,
    EVENT_HOLD,
    EVENT_SINGLE_PRESS,
    EVENT_TYPES,
    SIGNAL_BUTTON_EVENT,
)
from .coordinator import Flic2Coordinator, FlicConfigEntry
from .entity import Flic2Entity
from .flic2 import ButtonEvent, ButtonEventType

BUTTON_DESCRIPTION = EventEntityDescription(
    key="button",
    translation_key="button",
    event_types=EVENT_TYPES,
    device_class=EventDeviceClass.BUTTON,
)

# Map Flic2 ButtonEventType to our event types
# Note: Only CLICK should map to single_press. SINGLE_CLICK (type 3) is sent
# right before HOLD events as an internal state transition and should be ignored.
EVENT_TYPE_MAP: dict[ButtonEventType, str] = {
    ButtonEventType.CLICK: EVENT_SINGLE_PRESS,
    ButtonEventType.DOUBLE_CLICK: EVENT_DOUBLE_PRESS,
    ButtonEventType.HOLD: EVENT_HOLD,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FlicConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Flic 2 event entity from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([Flic2ButtonEvent(coordinator)])


class Flic2ButtonEvent(Flic2Entity, EventEntity):
    """Representation of a Flic 2 button event entity."""

    entity_description = BUTTON_DESCRIPTION

    def __init__(self, coordinator: Flic2Coordinator) -> None:
        """Initialize the event entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.button_uuid}_button"

    async def async_added_to_hass(self) -> None:
        """Subscribe to button events when entity is added."""
        await super().async_added_to_hass()

        # Subscribe to dispatcher events
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_BUTTON_EVENT}_{self.coordinator.address}",
                self._handle_button_event,
            )
        )

    @callback
    def _handle_button_event(self, event: ButtonEvent) -> None:
        """Handle a button event from the coordinator."""
        # Map the event type
        if event.event_type in EVENT_TYPE_MAP:
            event_type = EVENT_TYPE_MAP[event.event_type]
            self._trigger_event(
                event_type,
                {
                    "was_queued": event.was_queued,
                    "age_seconds": event.age_seconds if event.was_queued else 0,
                },
            )
            self.async_write_ha_state()
