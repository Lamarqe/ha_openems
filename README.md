
# ha_openems
Home Assistant integration that interfaces Fenecon FEMS and OpenEMS systems.

## Overview

* The integration provides full read and write access to Fenecon and OpenEMS systems. Every property which is accessible or configurable via the regular UI is made available equally in the Home Assistant UI.
* Integration of every backend property, even if not accessible via the regular UI.
* Backend system configuration is made fully visible and accessible to the Home Assistant UI:
	*	[Components](https://openems.github.io/openems.io/openems/latest/coreconcepts.html#_openems_component) are made available as Home Assistant service devices. 
	*	[Channels](https://openems.github.io/openems.io/openems/latest/coreconcepts.html#_channel) are made available as entities.\
   		<img src="screenshots/config_entry.jpg" width="600"/>
*	Pre-configured UI cards for Storage, Charging Station and Optimized Charges apps. These cards are equal in the offered functionality as the regular Fenecon/OpenEMS built-in UI components.
* Works with Fenecon and OpenEMS single and multi-edge systems.
* Advanced configuration options to support non-standard connection types.

## Table of contents

  * [Features / Common Use-cases](#features--common-use-cases)
  * [Installation](#installation)
  * [Configuration](#configuration)
  * [Disclaimer](#disclaimer) 

## Features / Common Use-cases:

### Energy Dashboard
Use the following entities to configure the Energy Dashboard:
<table>
  <tr><th>Area</th><th>Option</th><th>Component</th><th>Channel</th><th><i>comment</i></th></tr>
  <tr><td rowspan="2">Electricity Grid</td><td>Grid Consumption </td><td>_sum</td><td>GridBuyActiveEnergy</td><td></td></tr>
  <tr><td>Return to Grid </td><td>_sum</td><td>GridBuyActiveEnergy</td><td></td></tr>
  <tr><td rowspan="3">Solar Panels</td><td rowspan="3">Solar production </td><td>_sum</td><td>ProductionActiveEnergy</td><td><i>combined view</i></td></tr>
  <tr><td>pvInverter*</td><td>ActiveProductionEnergy</td><td rowspan="2"><i>individual view</i></td></tr>
  <tr><td>charger*</td><td>ActualEnergy</td></tr>
  <tr><td rowspan="4">Home Battery Storage</td><td rowspan="4">Battery systems</td><td>_sum</td><td>EssDcDischargeEnergy</td><td rowspan="2"><i>combined view</i></td></tr>
  <tr><td>_sum</td><td>EssDcChargeEnergy</td></tr>
  <tr><td>ess*</td><td>DcChargeEnergy</td><td rowspan="2"><i>individual view</i></td></tr>
  <tr><td>ess*</td><td>DcDischargeEnergy</td></tr>
</table>

### [Power Flow card plus](https://github.com/flixlix/power-flow-card-plus)
Use the following entities to configure the Power Flow card plus:
<table>
  <tr><th>Area</th><th>Option</th><th>Component</th><th>Channel</th></tr>
  <tr><td>Grid</td><td>Combined</td><td>_sum</td><td>GridActivePower</td></tr>
  <tr><td>Solar</td><td></td><td>_sum</td><td>ProductionActivePower</td></tr>
  <tr><td rowspan="2">Battery</td><td>Combined</td><td>_sum</td><td>EssDischargePower</td></tr>
  <tr><td>State of Charge</td><td>_sum</td><td>EssSoc</td></tr>
  <tr><td>Home</td><td></td><td>_sum</td><td>ConsumptionActivePower</td></tr>
</table>

### Apps (template UI cards)

To use the template cards, do the following:
1. The template cards depend on the following prerequisites (Please install via HACS):
     * [Decluttering card](https://github.com/custom-cards/decluttering-card)
     * [Config template card](https://github.com/iantrich/config-template-card)
     * [Button card](https://github.com/custom-cards/button-card)
     * [Template entity row](https://github.com/thomasloven/lovelace-template-entity-row)
3. Navigate to the dashboard in which you want to use the card
4. Select edit mode (pencil on the top right corner)
5. From the 3-dots menu in the top right corner, select "raw editor"
6. Paste the full contents of [openems_templates.yaml](openems_templates.yaml) before the dashboard contents
7. Go back to regular edit mode
8. add a new user-defined card, using yaml. 

*All texts are automatically translated to your Home Assistant default language*

#### EV Charging
**Sample Card config**
```
type: custom:decluttering-card
template: evcs_template
variables:
  - system: fems12345
  - evcs_id: 0
  - ctrlevcs_id: 0
```

**Card variables**
* `system`: Set to your FEMS/OpenEMS system name
* `evcs_id`: Set to your EVCS AppID. Eg `0` if your EVCS component is called `evcs0`. Default value: 0
* `ctrlevcs_id`: Set to your CtrlEVCS AppID. Eg `1` if your EVCS component is called `ctrlEvcs1`. Default value: 0

**Card screenshot** (German version)\
![image](screenshots/evcs.jpg)

#### Grid Optimized Charge
**Sample Card config**
```
type: custom:decluttering-card
template: optchrg_template
variables:
  - system: fems12345
  - ctrlgridoptimizedcharge_id: 0
```

**Card variables**
* `system`: Set to your FEMS/OpenEMS system name
* `ctrlgridoptimizedcharge_id`: Set to your grid optimized charge AppID. Eg `5` if your optchrg component is called `ctrlGridOptimizedCharge5`. Default value: 0

**Card screenshot** (German version)\
_Note: unapplicable options are automatically hidden and thus not visible in the screenshot_
![image](screenshots/gridoptimizedcharge.jpg)

#### Emergency Capacity Reserve
**Sample Card config**
```
type: custom:decluttering-card
template: ess_template
variables:
  - system: fems12345
  - ctrlemergencycapacityreserve_id: 0
```

**Card variables**
* `system`: Set to your FEMS/OpenEMS system name
* `ctrlemergencycapacityreserve_id`: Set to your emergency capacity AppID. Eg `0` if the component is called `ctrlEmergencyCapacityReserve0`. Default value: 0

**Card screenshot** (German version)\
![image](screenshots/ess.jpg)

### Additional commonly used components

#### BatteryInverter*
Contains entities for inverter properties like:
- temperatures of inverter, air, ...
  - `bmspacktemperature`
  - `airtemperature`
  - `radiatortemperature`
- The state of health: `bmssoh`
<img src="screenshots/batteryinverter.png" width="250"/>

#### CtrlIoHeatPump*
Here you will find the current state of your SG ready connected heatpump.\
<img src="screenshots/heatpump.jpg" width="250"/>

#### meter*
Here you find the grids frequency.\
<img src="screenshots/meter.jpg" width="400"/>

## Installation

### HACS

1. Install HACS (https://hacs.xyz/docs/setup/download)
2. Manually add [this repository](https://github.com/Lamarqe/ha_openems) to HACS
3. Select and enable "Fenecon FEMS & OpenEMS" Integration

## Configuration
Navigate to Settings -> Devices & Services -> Integrations. Click "Add Integration" and select "Fenecon FEMS & OpenEMS" from the list.
### Main configuration dialog
Enter your connection details: 
Parameter | Description
------ | ---------- 
Host | The hostname or IP address of your Fenecon or OpenEMS system. E.g. `fems12345` or `192.168.1.100`
Username | The username to access the system. Use `x` for local monitoring access. For FEMS online monitoring, your Email address is used as username
Password | For local monitoring, common values are `guest` or `owner`
Connection Type<br> | Choose 1 of the 5 supported connection types:<br><li>FEMS local monitoring (port 80)</li><li>OpenEMS local monitoring (port 8082)</li><li>Direct edge Websocket connection (port 8085). This is the standard connection type and pre-selected by default</li><li>Fenecon online monitoring at portal.fenecon.de (hostname option will not be used)</li><li>Custom Websocket URL (hostname option will not be used)</li>
Custom URL* | Custom Websocket URL, eg: `ws://192.168.1.100:8888/websocket`

*Options _Connection Type_ and _Custom URL_ are visible only to users who enabled **Advanced Mode**: In the bottom left, select your username to go to your [User profile](https://my.home-assistant.io/redirect/profile), and enable Advanced mode.

#### Selection of components and channels to be integrated
After setting up the config entry, there is a [default list](https://github.com/Lamarqe/ha_openems/blob/main/custom_components/openems/config/default_channels.json) of entities which is enabled. Entities for channels outside of these components are initially not created. Other entities within the created components are disabled by default.

In order to add or remove entities of additional or unwanted components, start the options flow by selecting the Configure option right next to the config entry name:

<img src="screenshots/options_flow.jpg" width="400"/>
Afterwards, a dialog will appear which allows to enable or disable each component in the configured system (screenshot shortened. Depending on your system configuration, there can be 20 to 50 components available in the list):

<img src="screenshots/options_dialog.jpg" width="200"/>

#### Select entities that will be active in Home Assistant 
Also for enabled components, most of the entities created are disabled by default. In order to adjust the selection, click the entities number of the config entry ("1038 entities" in the screenshot above).
This will open the entities dialog. In this dialog, (1) activate multi-select, (2) select the entities you want to enable or disable, then (3) enable / disable the selected items via the 3 dot menu on the top right:

<img src="screenshots/entity_selection.jpg" width="500"/>
**Only enabled entities will be subscribed in the backed!**

#### Check the backend for configuration changes, change connection details
In case of changes in the backend, users can check the backend for changes and with that update the config entries list of components and channels.
In order to do so, select the 3 dots next to the config entry. From the resulting menu, select **Reload**

Futhermore, in case the connection details shall be adjusted, you can use the same menu and select **Reconfigure**. This will bring up the main config dialog and allow you to make all necessary changes.

<img src="screenshots/reload_reconfigure.jpg" width="600"/>



## Disclaimer

### Tested Setups

* Fenecon Home 10 systems with 
	* EV Chargers
		* Keba 
		* Hardy Barth
	* FEMS Relaisboard
	* Heatpumps (Fenecon App Power to Heat)
		*  Vaillant

### Limitations

- This integration is not affiliated with Fenecon.
	- Fenecon might change APIs with upcoming version.
	- The developers take no responsibility for anything that happens to your equipment due to this integration. I.e., 
		- Please be careful with controlling entities which are not enabled by default.
		-  By default, only a few pre-configured channels are enabled per component. However, there is no limitation how many channels can be enabled in parallel. Please be careful about expanding to very large numbers (multiple hundreds).
