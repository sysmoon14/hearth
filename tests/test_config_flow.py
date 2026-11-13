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
