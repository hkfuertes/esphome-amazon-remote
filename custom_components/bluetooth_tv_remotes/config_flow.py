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

from .const import (
    CONF_MAC,
    CONF_OUTPUT_SPEAKER,
    CONF_REMOTE_TYPE,
    DOMAIN,
    REMOTE_MODELS,
    REMOTE_TYPE_AMAZON,
    REMOTE_TYPE_AUTO,
    REMOTE_TYPE_CHROMECAST,
)
from .protocol import detect_remote_type, discover_hidraw_devices, normalize_mac


def _speaker_selector() -> EntitySelector:
    return EntitySelector(
        EntitySelectorConfig(domain="media_player", multiple=False)
    )


def _remote_type_selector() -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=REMOTE_TYPE_AUTO, label="Auto-detect"),
                SelectOptionDict(
                    value=REMOTE_TYPE_AMAZON, label="Amazon Fire TV Remote"
                ),
                SelectOptionDict(
                    value=REMOTE_TYPE_CHROMECAST, label="Chromecast Remote"
                ),
            ],
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class TVRemoteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bluetooth TV Remotes."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        discovered = await self.hass.async_add_executor_job(discover_hidraw_devices)
        configured_macs = {entry.unique_id for entry in self._async_current_entries()}

        if user_input is not None:
            try:
                mac = normalize_mac(user_input[CONF_MAC])
            except ValueError:
                errors[CONF_MAC] = "invalid_mac"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                chosen_type = user_input.get(CONF_REMOTE_TYPE, REMOTE_TYPE_AUTO)
                if chosen_type == REMOTE_TYPE_AUTO:
                    info = discovered.get(mac, {})
                    remote_type = (
                        detect_remote_type(info.get("hid_id", ""))
                        or REMOTE_TYPE_AMAZON
                    )
                else:
                    remote_type = chosen_type
                _, _, display_name = REMOTE_MODELS.get(
                    remote_type, REMOTE_MODELS[REMOTE_TYPE_AMAZON]
                )
                speaker = (user_input.get(CONF_OUTPUT_SPEAKER) or "").strip()
                options = {CONF_OUTPUT_SPEAKER: speaker} if speaker else {}
                return self.async_create_entry(
                    title=f"{display_name} ({mac[-5:]})",
                    data={CONF_MAC: mac, CONF_REMOTE_TYPE: remote_type},
                    options=options,
                )

        available = {
            mac: info
            for mac, info in discovered.items()
            if mac not in configured_macs
        }
        if available:
            sorted_items = sorted(
                available.items(),
                key=lambda item: (
                    detect_remote_type(item[1].get("hid_id", "")) is None,
                    item[0],
                ),
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
                vol.Required(CONF_REMOTE_TYPE, default=REMOTE_TYPE_AUTO): _remote_type_selector(),
                vol.Optional(CONF_OUTPUT_SPEAKER): _speaker_selector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
