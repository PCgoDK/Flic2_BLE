# Flic 2 BLE Integration for Home Assistant

<p align="center">
  <img src="custom_components/flic_ble/brand/logo.png" alt="Flic 2 BLE logo" width="160" />
</p>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/PCgoDK/Flic2_BLE.svg)](https://github.com/PCgoDK/Flic2_BLE/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Home Assistant custom integration for Flic 2 smart buttons using direct Bluetooth Low Energy (BLE) communication. No Flic Hub required.

## Features

- Direct BLE pairing with Flic 2 buttons (no hub needed)
- Button press events: single press, double press, and hold
- Battery level monitoring
- Device triggers for easy automation
- Auto-discovery of nearby Flic buttons
- Persistent pairing across restarts
- Support for queued events (events that occurred while disconnected)

## Requirements

- Home Assistant 2024.1.0 or newer
- Bluetooth adapter on your Home Assistant host
- Flic 2 button(s) - not paired to the Flic app or hub

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu in the top right corner
3. Select "Custom repositories"
4. Add `https://github.com/PCgoDK/Flic2_BLE` with category "Integration"
5. Click "Add"
6. Search for "Flic 2 BLE" and install it
7. Restart Home Assistant

### Manual Installation

1. Download the latest release from [GitHub](https://github.com/PCgoDK/Flic2_BLE/releases)
2. Extract and copy the `custom_components/flic_ble` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

### Pairing a New Button

1. **Unpair from Flic app**: If your button is paired to the Flic mobile app, you must unpair it first. Open the Flic app and remove the button.

2. **Add the integration**: Go to **Settings > Devices & Services > Add Integration** and search for "Flic 2 BLE"

3. **Discovery**: Home Assistant will scan for nearby Flic buttons. Make sure your button is:
   - Not connected to any other device
   - Within Bluetooth range
   - Has battery (press it to wake it up)

4. **Pair**: Select your button from the discovered list and follow the pairing process. You may need to hold the button for a few seconds during pairing.

5. **Done**: Once paired, the button will appear as a device with event and sensor entities.

### Troubleshooting Pairing

- **Button not discovered**: Press the button a few times to wake it up, then retry discovery
- **Pairing fails**: Hold the button for 8+ seconds to factory reset it, then try again
- **Still paired to Flic app**: Ensure you've removed the button from the Flic mobile app

## Entities

Each Flic 2 button creates the following entities:

### Event Entity

**Entity ID**: `event.flic_2_<name>_button`

Fires events for button presses with the following event types:

| Event Type | Description |
|------------|-------------|
| `single_press` | Single button click |
| `double_press` | Two quick clicks |
| `hold` | Button held down |

Event data includes:
- `was_queued`: Boolean indicating if the event was queued while disconnected
- `age_seconds`: Age of the event in seconds (for queued events)

### Battery Sensor

**Entity ID**: `sensor.flic_2_<name>_battery`

Reports the battery level as a percentage (0-100%).

## Automations

### Using Device Triggers (Recommended)

The easiest way to automate Flic buttons is with device triggers:

```yaml
automation:
  - alias: "Toggle Living Room Light on Flic Press"
    trigger:
      - platform: device
        device_id: <your_flic_device_id>
        domain: flic_ble
        type: single_press
    action:
      - service: light.toggle
        target:
          entity_id: light.living_room
```

### Using Event Entity

You can also trigger automations from the event entity state:

```yaml
automation:
  - alias: "Flic Double Press - Movie Mode"
    trigger:
      - platform: state
        entity_id: event.flic_2_bedroom_button
        attribute: event_type
        to: "double_press"
    action:
      - service: scene.turn_on
        target:
          entity_id: scene.movie_mode
```

### Multiple Actions Example

```yaml
automation:
  - alias: "Flic Multi-Action Controller"
    trigger:
      - platform: device
        device_id: <your_flic_device_id>
        domain: flic_ble
        type: single_press
        id: single
      - platform: device
        device_id: <your_flic_device_id>
        domain: flic_ble
        type: double_press
        id: double
      - platform: device
        device_id: <your_flic_device_id>
        domain: flic_ble
        type: hold
        id: hold
    action:
      - choose:
          - conditions:
              - condition: trigger
                id: single
            sequence:
              - service: light.toggle
                target:
                  entity_id: light.bedroom
          - conditions:
              - condition: trigger
                id: double
            sequence:
              - service: media_player.play_pause
                target:
                  entity_id: media_player.spotify
          - conditions:
              - condition: trigger
                id: hold
            sequence:
              - service: script.turn_on
                target:
                  entity_id: script.goodnight
```

## Troubleshooting

### Button Not Responding

1. **Check battery**: View the battery sensor - replace if below 10%
2. **Check Bluetooth**: Ensure your Bluetooth adapter is working in Home Assistant
3. **Distance**: Move the button closer to your Home Assistant host
4. **Restart**: Try restarting the integration from the device page

### Events Not Firing

1. **Check entity**: Ensure the event entity is enabled
2. **Check logs**: Look for errors in Home Assistant logs related to `flic_ble`
3. **Re-pair**: Remove and re-add the button if issues persist

### Connection Issues

The integration automatically reconnects when the button comes back in range. If you experience frequent disconnections:

1. Reduce distance between button and Bluetooth adapter
2. Check for Bluetooth interference from other devices
3. Ensure only one Home Assistant instance is trying to connect to the button

### Debug Logging

Enable debug logging to troubleshoot issues:

```yaml
logger:
  default: info
  logs:
    custom_components.flic_ble: debug
```

## Technical Details

| Property | Value |
|----------|-------|
| Domain | `flic_ble` |
| Version | 1.0.0 |
| Manufacturer | Shortcut Labs |
| IoT Class | Local Push |
| Bluetooth Service UUID | `00420000-8f59-4420-870d-84f3b617e493` |

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests on [GitHub](https://github.com/PCgoDK/Flic2_BLE).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Disclaimer

This integration is not affiliated with or endorsed by Shortcut Labs (the makers of Flic). Flic is a trademark of Shortcut Labs AB.
