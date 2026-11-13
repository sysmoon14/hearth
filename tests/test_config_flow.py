"""Setting up and adding rooms."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import NEST, OFFICE_SENSOR, OFFICE_TRV


async def test_setup_and_add_room(hass: HomeAssistant, house, calls) -> None:
    result = await hass.config_entries.flow.async_init("hearth", context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"boiler": NEST})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "add_radiator"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"name": "Office", "trvs": [OFFICE_TRV], "sensor": OFFICE_SENSOR}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["zones"][0]["id"] == "office"
    assert hass.states.get("climate.office") is not None


async def test_settings_can_clear_the_switch(hass: HomeAssistant, house, calls) -> None:
    from .conftest import hearth_entry

    entry = hearth_entry(hass, [], boiler_switch="switch.nest_call_for_heat", backup_zone="office")
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "settings"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"boiler": NEST})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "boiler_switch" not in entry.options
    assert "backup_zone" not in entry.options
    assert entry.options["boiler"] == NEST
    assert entry.options["away_temp"] == 16.0
