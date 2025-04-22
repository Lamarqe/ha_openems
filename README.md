# ha_openems
Home Assistant component that interfaces FEMS and OpenEMS, mainly used in Fenecon inverters.

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

## Remarks

* The integration just transparently publishes structures as they are existing in the backend. In result, there is a _sum device and all its channels below it.
* This implementation allows to configure calculations for number properties in number_properties.json. So starting with v0.6, _PropertyForceChargeMinPower shows and consumes total numbers which get converted to phase based values before showing/sending them back to the backend, same as it is also in the regular UI.

## Installation

### HACS

1. Install HACS (https://hacs.xyz/docs/setup/download)
2. Manually add this repository to HACS
4. Add FEMS Integration
6. Enter your OpenEMS / FEMS address and user account (Fenecon standard:  x / user)

The Fenecon password (user or owner) controls your access rights. If you just want to monitor (read-only access), "user" is fine. If you want to change settings, you need to use "owner". The Owner permissions work even without REST/API Write App.

## Installation

After installation most devices and entities are disabled. You can enable them like this:

<img src="https://github.com/user-attachments/assets/3c4619fa-41a7-4b20-a4f5-46b1fd692cfc" width="750">


Some devices to watch for:

1. ctrlIoHeatPumpX
2. meterX
3. timeOfUseTariffX
4. batteryInverterX
5. chargerX (these are inverter strings)

The numbers (X) can vary from instalation to installation and model to model. E.g. a Fenecon Home 10 Gen 10 has two inverters, so you would get 0 and 1, if you use both of them. Other models might have different numbers of strings, or maybe you don't use them all. Or you have two battery-towers connected, ...

Some devices are only active, if you have the corresponding app installed:
* ctrlIoHeatPumpX = FEMS App Power-to-Heat
* timeOfUseTariffX = FEMS App Dynamischer Stromtarif
* evcsX = FEMS App AC-Ladestation

Some Apps bring two devices into the integration: the wallbox (AC-Ladestation) enbales evcsX and ctrlEvcs0. One is for configuration and one for monitoring.

## Entites

### _ssum
The most relevant entities (for most people) are in the _sum device:

<img src="https://github.com/user-attachments/assets/95507715-3e03-43f3-ae13-e46cf0ffc5e2" width="200">

Here you can find your power production, consumption, battery charge and many more.

### ChargerX
Here you can find voltage, current and power for your strings.

<img src="https://github.com/user-attachments/assets/9d09b670-0d72-44dc-b905-f1b5cb0097f3" width="200">

### BatteryInverterX
Has the temperatures for radiator, air, ...

* _batteryinverter0_bmspacktemperature
* _batteryinverter0_airtemperature
* _batteryinverter0_radiatortemperature

And the state of health
* _batteryinverter0_bmssoh

<img src="https://github.com/user-attachments/assets/963ef819-9a25-4512-9452-5a81ed598a66" width="200">

### ctrlIoHeatPumpX
Here you will find the current state of your SG ready connected heatpump.

<img src="https://github.com/user-attachments/assets/c72105a4-1b37-405c-9122-823ce809e59a" width="200">

You can also manually set the SD Ready modes:

<img src="https://github.com/user-attachments/assets/ca2c73f7-65e3-4481-8aa6-782733fcdfc8" width="200">

### evcsX and ctrlEvcs0 (Wallbox)
Here you find the configuration and the energy statistics for your connected wallbox:

<img src="https://github.com/user-attachments/assets/e7097dee-fb33-421b-8aa3-2d5d5ecd9148" width="200">

### meterX
Here you find the grids frequency.

<img src="https://github.com/user-attachments/assets/6c04020a-ec77-4215-b505-a80f466f887d" width="200">

### ctrlGridOptimizedChargeX (Netzdienliche Beladung)

Here you can enable or disable the delayed battery charging.

### Energy Dashboard 
For the energy dashboard you need these entities:
* _sum_GridBuyActiveEnergy
* _sum_GridSellActiveEnergy
* _sum_productionactiveenergy
* _batteryinverter0_dcchargeenergy
* _batteryinverter0_dcdischargeenergy

<img src="https://github.com/user-attachments/assets/196cd51d-f0d6-466f-81f3-7c4a3ed0f383" width="250">

Some of these entities are disabled by default. You need to enable them and wait some minutes before adding them to the energy dashboard. Home Assistant needs some time to initialize the statistics for these entities. Before this is done, Home Assistant will not accept these entities.
