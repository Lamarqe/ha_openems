# ha_openems
Home Assistant component that interfaces FEMS and OpenEMS, mainly used in Fenecon inverters,

> [!WARNING] 
> This integration is not affiliated with Fenecon, the developers take no responsibility for anything that happens to
> your devices because of this integratation.

## Tested Setups

* Fenecon Home 10 with Keba Wallbox, FEMS Relaiboard and Vaillant Heatpump via Fenecon App Power to Heat

## Features

* Can be set up via the Home Assistant UI
* Retrieves all devices (implementation is ready to handle multi-edge and single-edge configuration. Currently, only single-edge setup is tested)
* Retrieves every channel from the connected system and creates according entities
* Currently, there is a hard-coded list of ~50 channels whose entities are enabled by default. All other entities are created, but disabled by default. Every entity * can be enabled and disabled via the Home Assistant UI
* Enabled entities are updated in Home Assistant as soon as OpenEMS pushes updates via the WebSocket connection. Currently, there is no throttling, but it could be easily added if necessary for performance reasons in Home Assistant.
* There is currently no limitation how many channels can be enabled in parallel. The by default enabled 50 entities represent exactly as what also the WebUI subscribes to after the login. However, be careful with expanding to very large numbers. I don’t know what amount OpenEMS can handle (or if these subscription have a performance relevance at all)

## Installation

### HACS

1. [Install HACS](https://hacs.xyz/docs/setup/download)
2. Add this repository manually intHACS
3. Restart Home Assistant
4. Add FEMS integration
5. Enter your inverters IP-Address and user account (user/user for Fenecon)
