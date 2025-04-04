# ha_openems
Home Assistant component that interfaces FEMS and OpenEMS, mainly used in Fenecon inverters,

> [!WARNING] 
> This integration is not affiliated with Fenecon, the developers take no responsibility for anything that happens to your equipment due to this integration.

## Tested Setups

* Fenecon Home 10 with Keba Wallbox, FEMS Relaisboard and Vaillant Heatpump via Fenecon App Power to Heat

## Features

* Can be set up via the Home Assistant UI
* Retrieves all devices (implementation is ready to handle multi-edge and single-edge configuration. Currently, only single-edge setup is tested)
* Retrieves every channel from the connected system and creates according entities
* Currently, there is a hardcoded list of ~50 channels whose entities are enabled by default. All other entities are created, but disabled by default. Every entity * can be enabled and disabled via the Home Assistant UI
* Enabled entities will be updated in Home Assistant as soon as OpenEMS pushes updates via the WebSocket connection. There is currently no throttling, but it could easily be added to Home Assistant if needed for performance reasons.
* There is currently no limitation how many channels can be enabled in parallel. The by default enabled 50 entities represent exactly as what also the WebUI subscribes to after the login. However, you should be careful about expanding to very large numbers. I  don't know what amount OpenEMS can handle (or if these subscriptions have any performance relevance at all)

## Installation

### HACS

1. Install HACS (https://hacs.xyz/docs/setup/download)
2. Manually add this repository to HACS
4. Add FEMS Integration
6. Enter your OpenEMS / FEMS address and user account (Fenecon standard:  x / user)

## Installation

After installation most devices and entities are disabled. You can enable them like this:

![Bildschirmfoto 2025-04-04 um 18 57 36](https://github.com/user-attachments/assets/7e74abd1-b330-4594-a1c9-25d149a1e2fb)

Some devices to watch for:

1. ctrlIoHeatPump0
2. meter0
3. timeOfUseTariff0
4. batteryInverter0
5. charger0 and charger1 (these are inverter strings)

## Entites

### _ssum
The most relevant entite (for most people) are in the _sum device:
![Bildschirmfoto 2025-04-04 um 19 17 40](https://github.com/user-attachments/assets/95507715-3e03-43f3-ae13-e46cf0ffc5e2)
Here you can find your power production, consumption, battery charge and many more.

### Charger0 and 1
Here you can find voltage, current and power for both of your strings.

### BatteryInverter
Has the temperatures for radiator, air, ...
![Bildschirmfoto 2025-04-04 um 19 33 24](https://github.com/user-attachments/assets/d8e0df40-8a91-41d9-b04f-3b9e1d402193)

### ctrlIoHeatPump0
Here you will find the current state of your SG ready connected heatpump.

![Bildschirmfoto 2025-04-04 um 19 32 56](https://github.com/user-attachments/assets/c4f8944f-7262-4b35-8d5c-00d4e9eb61ba)

### Wallbox
Here you find the configuration and the energy statistics for your connected wallbox:

![Bildschirmfoto 2025-04-04 um 19 30 37](https://github.com/user-attachments/assets/e7097dee-fb33-421b-8aa3-2d5d5ecd9148)

### meter0
Here you find the grids frequency.

![Bildschirmfoto 2025-04-04 um 19 34 00](https://github.com/user-attachments/assets/093c8a62-87ab-4a92-bcc2-b2ee67163512)

