"""Constants for the HA OpenEMS integration."""

DOMAIN: str = "openems"

ATTR_VALUE: str = "value"
ATTR_PROPERTIES: str = "properties"
ATTR_TIMEOUT: str = "timeout"
ATTR_UPDATE_CYCLE: str = "update_cycle"

CONF_EDGES: str = "edges"
CONF_EDGE: str = "edge"
CONF_MORE_OPTIONS: str = "more_options"
CONF_ADVANCED_OPTIONS: str = "advanced_options"
CONF_FORWARD_INTERVAL: str = "forward_interval"
CONF_IGNORE_DECREASING_IF_TOTAL_INCREASING: str = (
    "ignore_decreasing_if_total_increasing"
)

QUERY_CONFIG_VIA_REST: bool = False


# Channels enabled by default, keyed by component id regexp
DEFAULT_CHANNELS: dict[str, frozenset[str]] = {
    r"_meta": frozenset({"_PropertyIsEssChargeFromGridAllowed"}),
    r"_sum": frozenset(
        {
            "ConsumptionActivePower",
            "ConsumptionMaxActivePower",
            "EssActivePower",
            "EssDcChargeEnergy",
            "EssDcDischargeEnergy",
            "EssDischargePower",
            "EssMaxDischargePower",
            "EssMinDischargePower",
            "EssSoc",
            "GridActivePower",
            "GridBuyActiveEnergy",
            "GridMaxActivePower",
            "GridMinActivePower",
            "GridMode",
            "GridSellActiveEnergy",
            "ProductionAcActivePower",
            "ProductionActiveEnergy",
            "ProductionActivePower",
            "ProductionDcActualPower",
            "ProductionMaxActivePower",
            "State",
        }
    ),
    r"charger\d+": frozenset({"ActualEnergy", "ActualPower"}),
    r"ctrlEmergencyCapacityReserve\d+": frozenset(
        {"_PropertyIsReserveSocEnabled", "_PropertyReserveSoc"}
    ),
    r"ctrlEvseCluster\d+|ctrlEvseSingle\d+": frozenset(
        {
            "ActualMode",
            "SessionEnergy",
            "StateMachine",
            "_PropertyManualEnergySessionLimit",
            "_PropertyMode",
            "_PropertyPhaseSwitching",
        }
    ),
    r"ctrlEvcs\d+": frozenset(
        {
            "_PropertyChargeMode",
            "_PropertyDefaultChargeMinPower",
            "_PropertyEnabledCharging",
            "_PropertyEnergySessionLimit",
            "_PropertyForceChargeMinPower",
            "_PropertyPriority",
        }
    ),
    r"ctrlEssLimiter14a\d+": frozenset({"RestrictionMode"}),
    r"ctrlGridOptimizedCharge\d+": frozenset(
        {
            "DelayChargeMaximumChargeLimit",
            "DelayChargeState",
            "SellToGridLimitMinimumChargeLimit",
            "SellToGridLimitState",
            "TargetMinute",
            "_PropertyDelayChargeRiskLevel",
            "_PropertyManualTargetTime",
            "_PropertyMode",
        }
    ),
    r"ctrlIoHeatPump\d+": frozenset(
        {
            "Status",
            "_PropertyAutomaticForceOnCtrlEnabled",
            "_PropertyAutomaticForceOnSoc",
            "_PropertyAutomaticForceOnSurplusPower",
            "_PropertyAutomaticLockCtrlEnabled",
            "_PropertyAutomaticLockGridBuyPower",
            "_PropertyAutomaticLockSoc",
            "_PropertyAutomaticRecommendationCtrlEnabled",
            "_PropertyAutomaticRecommendationSurplusPower",
            "_PropertyManualState",
            "_PropertyMinimumSwitchingTime",
            "_PropertyMode",
        }
    ),
    r"ctrlPrepareBatteryExtension\d+": frozenset(
        {
            "CtrlIsBlockingEss",
            "CtrlIsChargingEss",
            "CtrlIsDischargingEss",
            "_PropertyIsRunning",
            "_PropertyTargetTime",
            "_PropertyTargetTimeSpecified",
        }
    ),
    r"ess\d+": frozenset(
        {"Capacity", "DcChargeEnergy", "DcDischargeEnergy", "DcDischargePower", "Soc"}
    ),
    r"evcs\d+": frozenset(
        {
            "ActivePower",
            "ChargePower",
            "EnergySession",
            "MaximumHardwarePower",
            "MinimumHardwarePower",
            "Phases",
            "SetChargePowerLimit",
            "State",
            "Status",
        }
    ),
    r"meter\d+": frozenset({"ActivePower"}),
    r"pvInverter\d+": frozenset({"ActivePower", "ActiveProductionEnergy"}),
}
