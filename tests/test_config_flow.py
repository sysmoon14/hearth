"""Setting up Hearth, and moving an older entry's settings forward."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import OFFICE, RELAY


async def test_setup_asks_for_the_switch(hass: HomeAssistant, house, calls) -> None:
    result = await hass.config_entries.flow.async_init("hearth", context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"boiler_switch": RELAY})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.options == {"boiler_switch": RELAY, "zones": []}
    assert entry.minor_version == 3


async def test_configure_points_to_the_panel(hass: HomeAssistant, house, calls) -> None:
    from .conftest import hearth_entry

    entry = hearth_entry(hass, [])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "use_panel"


async def test_an_older_entry_drops_the_thermostat_settings(hass: HomeAssistant, house, calls) -> None:
    entry = MockConfigEntry(
        domain="hearth", title="Hearth", data={}, version=1, minor_version=1,
        options={
            "boiler": "climate.nest", "boiler_switch": RELAY, "boiler_on_temp": 25, "boiler_off_temp": 10,
            "fallback_temp": 17, "backup_zone": "bedroom", "away_temp": 15.5, "zones": [OFFICE],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == 3
    assert entry.options == {"boiler_switch": RELAY, "away_temp": 15.5, "zones": [OFFICE]}
    assert hass.states.get("climate.office") is not None
