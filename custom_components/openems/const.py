"""Constants for the HA OpenEMS integration."""

STORAGE_VERSION = 1
STORAGE_KEY = "openems_config"

DEFAULT_EDGE_CHANNELS = [
    "_sum/State",
    "_sum/EssSoc",
    "_sum/EssActivePower",
    "_sum/EssMinDischargePower",
    "_sum/EssMaxDischargePower",
    "_sum/GridActivePower",
    "_sum/GridMinActivePower",
    "_sum/GridMaxActivePower",
    "_sum/GridMode",
    "_sum/ProductionActivePower",
    "_sum/ProductionDcActualPower",
    "_sum/ProductionAcActivePower",
    "_sum/ProductionMaxActivePower",
    "_sum/ConsumptionActivePower",
    "_sum/ConsumptionMaxActivePower",
    "_sum/EssActivePowerL1",
    "_sum/EssActivePowerL2",
    "_sum/EssActivePowerL3",
    "ctrlPrepareBatteryExtension0/CtrlIsBlockingEss",
    "ctrlPrepareBatteryExtension0/CtrlIsChargingEss",
    "ctrlPrepareBatteryExtension0/CtrlIsDischargingEss",
    "ctrlPrepareBatteryExtension0/_PropertyIsRunning",
    "ctrlPrepareBatteryExtension0/_PropertyTargetTimeSpecified",
    "ctrlPrepareBatteryExtension0/_PropertyTargetTime",
    "ctrlEmergencyCapacityReserve0/_PropertyReserveSoc",
    "ctrlEmergencyCapacityReserve0/_PropertyIsReserveSocEnabled",
    "charger0/ActualPower",
    "charger1/ActualPower",
    "ess0/Soc",
    "ess0/Capacity",
    "_sum/GridActivePowerL1",
    "_sum/GridActivePowerL2",
    "_sum/GridActivePowerL3",
    "ctrlEssLimiter14a0/RestrictionMode",
    "_sum/ConsumptionActivePowerL1",
    "_sum/ConsumptionActivePowerL2",
    "_sum/ConsumptionActivePowerL3",
    "evcs0/ActivePower",
    "evcs0/ActivePowerL1",
    "evcs0/ActivePowerL2",
    "evcs0/ActivePowerL3",
    "meter2/ActivePower",
    "meter2/ActivePowerL1",
    "meter2/ActivePowerL2",
    "meter2/ActivePowerL3",
    "evcs0/ChargePower",
    "evcs0/Phases",
    "evcs0/Plug",
    "evcs0/Status",
    "evcs0/State",
    "evcs0/EnergySession",
    "evcs0/MinimumHardwarePower",
    "evcs0/MaximumHardwarePower",
    "evcs0/SetChargePowerLimit",
    "ctrlEvcs0/_PropertyEnabledCharging",
    "ctrlGridOptimizedCharge0/DelayChargeState",
    "ctrlGridOptimizedCharge0/SellToGridLimitState",
    "ctrlGridOptimizedCharge0/DelayChargeMaximumChargeLimit",
    "ctrlGridOptimizedCharge0/SellToGridLimitMinimumChargeLimit",
    "ctrlGridOptimizedCharge0/_PropertyMode",
    "ess0/DcDischargePower",
    "pvInverter0/ActivePower",
]
