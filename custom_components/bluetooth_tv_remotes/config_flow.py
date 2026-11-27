"""Config and options flows for Bluetooth TV Remotes."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (  # noqa: TID252
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.helpers.selector import (  # noqa: TID252
    EntitySelector,
    EntitySelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import AMAZON_VENDOR_ID, CONF_MAC, CONF_OUTPUT_SPEAKER, DOMAIN
from .protocol import discover_hidraw_devices, normalize_mac


def _speaker_selector() -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="media_player", multiple=False)
    )


class TVRemoteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bluetooth TV Remotes."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        discovered = await self.hass.async_add_executor_job(discover_hidraw_devices)

        if user_input is not None:
            try:
                mac = normalize_mac(user_input[CONF_MAC])
            except ValueError:
                errors[CONF_MAC] = "invalid_mac"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                speaker = (user_input.get(CONF_OUTPUT_SPEAKER) or "").strip()
                options = {CONF_OUTPUT_SPEAKER: speaker} if speaker else {}
                return self.async_create_entry(
                    title=f"Amazon Fire TV Remote ({mac[-5:]})",
                    data={CONF_MAC: mac},
                    options=options,
                )

        if discovered:
            sorted_items = sorted(
                discovered.items(),
                key=lambda item: (AMAZON_VENDOR_ID not in item[1].get("hid_id", ""), item[0]),
            )
            mac_options = [
                SelectOptionDict(
                    value=mac,
                    label=f"{info['name']} ({mac}) — {info['dev']}",
                )
                for mac, info in sorted_items
            ]
            mac_field = SelectSelector(
                SelectSelectorConfig(
                    options=mac_options,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            )
            default_mac = sorted_items[0][0]
            mac_key: Any = vol.Required(CONF_MAC, default=default_mac)
        else:
            mac_field = str
            mac_key = vol.Required(CONF_MAC)

        schema = vol.Schema(
            {
                mac_key: mac_field,
                vol.Optional(CONF_OUTPUT_SPEAKER): _speaker_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
