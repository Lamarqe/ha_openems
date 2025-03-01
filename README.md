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
* There is currently no limitation how many channels can be enabled in parallel. The by default enabled 50 entities represent exactly as what also the WebUI subscribes to after the login. However, you should be careful about expanding to very large numbers. I  don't  know what amount OpenEMS can handle (or if these subscriptions have any performance relevance at all)

## Installation

### HACS

1. Install HACS (https://hacs.xyz/docs/setup/download)
2. Manually add this repository manually into HACS
3. Restart Home Assistant
4. Add FEMS tntegration
6. Enter your inverters IP address and user account (Fenecons default is user/user)
