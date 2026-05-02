"""Helper methods using openems classes, eg during channel creation."""

import re
from typing import TYPE_CHECKING

from jinja2 import Template

from .const import SLASH_ESC

if TYPE_CHECKING:
    from .openems import OpenEMSComponent


def prepare_ref_value(
    expr: str, component: OpenEMSComponent
) -> tuple[Template, list[str]]:
    """Parse a template string into a template and channels contained."""
    linked_channels = []

    def calc_component_reference(matchobj) -> str:
        nonlocal linked_channels
        match = matchobj.group()[2:-2]
        if "/" in match:
            comp_ref, channel = match.split("/")
            if comp_ref[0] == "$":
                # if the reference starts with $, treat the component like a variable,
                # to be looked up in the component properties
                # replace all linked channels with their values
                comp_ref = component.json_properties[comp_ref[1:]]
        else:
            comp_ref, channel = component.name, match

        # prepare value containers of required channels
        linked_channel = comp_ref + SLASH_ESC + channel
        if linked_channel not in linked_channels:
            linked_channels.append(linked_channel)
        return linked_channel

    value_expr = "{{" + re.sub(r"{{(.*?)}}", calc_component_reference, expr) + "}}"
    return Template(value_expr), linked_channels


def expand_sensor_def(
    sensor_def: dict[str, str], channel_ids: list[str]
) -> list[dict[str, str]]:
    """Expand a sensor definition with variables in the id and template."""
    # find all variables in the sensor template
    var_pattern = re.compile(r"{{(.*?)}}")
    refs = var_pattern.findall(sensor_def["template"])

    # create corresponding regexps to apply group matches against it afterwards
    template_variables: list[tuple[str, str]] = []
    pattern_matched = False
    for ref in refs:
        ref_pattern, num_subs = re.subn(r"\{(\w+)\}", r"(?P<\1>[^{}]+)", ref)
        pattern_matched |= num_subs > 0
        template_variables.append((ref, ref_pattern))
    # if no variables need to be expanded, return the original sensor definition as a single item list
    if not pattern_matched:
        return [sensor_def]

    key_groups: list[str] = re.findall(r"\{(\w+)\}", sensor_def["id"])

    # try to match all channel ids to the variables, and find all channels that match the variable pattern
    target_defs: dict[tuple, list[dict[str, str]]] = {}
    for t in template_variables:
        for channel_id in channel_ids:
            if match := re.fullmatch(t[1], channel_id):
                keys = [v for k, v in match.groupdict().items() if k in key_groups]
                key_tuple = tuple(keys)
                values = {
                    k: v for k, v in match.groupdict().items() if k not in key_groups
                }
                if key_tuple not in target_defs:
                    target_defs[key_tuple] = []
                if values not in target_defs[key_tuple]:
                    target_defs[key_tuple].append(values)

    # create new sensor defs for all found variable matches
    expanded_defs = []
    for key_tuple, values_list in target_defs.items():
        # as the tuple cannot be used directly for string formatting,
        # create a mapping of the variable names to the values
        # initially populate the mapping with the key groups
        key_map = {}
        for i, key in enumerate(key_tuple):
            key_map[key_groups[i]] = key
        expanded_id = sensor_def["id"].format(**key_map)
        expanded_template_vars = {}
        # now merge the key map with the template groups and process the template with the combined map
        for template_var, _ in template_variables:
            expanded_template_vars[template_var] = []
            for values in values_list:
                all_values = {**key_map, **values}

                expanded_template_vars[template_var].append(
                    template_var.format(**all_values)
                )
            # replace the resulting list with a string representation of the content, to be used in the template
            expanded_template_vars[template_var] = (
                "{{" + "}}, {{".join(expanded_template_vars[template_var]) + "}}"
            )
            expanded_def = re.sub(
                var_pattern,
                lambda m, expanded_template_vars=expanded_template_vars: (
                    expanded_template_vars.get(m.group(1), m.group(0))
                ),
                sensor_def["template"],
            )
        # copy original attributes, only replace the template and id with the expanded ones
        added_def = sensor_def.copy()
        added_def["template"] = expanded_def
        added_def["id"] = expanded_id
        expanded_defs.append(added_def)

    return expanded_defs
