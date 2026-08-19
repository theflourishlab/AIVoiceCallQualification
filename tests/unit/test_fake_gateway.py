import pytest

from becca.telnyx.fake_gateway import FakeTelnyxGateway
from becca.telnyx.gateway import TelnyxNotFound


async def test_full_spike_lifecycle() -> None:
    gw = FakeTelnyxGateway()
    insight_id = await gw.create_insight(name="still_attending", instructions="…", json_schema=None)
    group_id = await gw.create_insight_group(name="g")
    await gw.assign_insight_to_group(group_id=group_id, insight_id=insight_id)
    assert gw.groups[group_id] == [insight_id]

    assistant = await gw.create_assistant(
        name="a",
        model="moonshotai/Kimi-K2.6",
        instructions="Hello {{first_name}}",
        greeting="Hi",
        voice="Telnyx.NaturalHD.astra",
        insight_group_id=group_id,
        dynamic_variables={"first_name": "Chidinma"},
    )
    call = await gw.place_call(
        connection_id=assistant.default_texml_app_id,
        assistant_id=assistant.id,
        to="+2348030001188",
        from_="+23418884120",
        variables={"first_name": "Chidinma"},
        metadata={"becca_agent_id": "1"},
        record=False,
        status_callback="http://localhost/webhooks/telnyx",
        amd_status_callback="http://localhost/webhooks/telnyx",
    )
    assert call["to"] == "+2348030001188"


async def test_assistant_delete_does_not_cascade_to_texml_app() -> None:
    """The fake preserves the tested FR-LAUNCH-6 behaviour."""
    gw = FakeTelnyxGateway()
    group_id = await gw.create_insight_group(name="g")
    assistant = await gw.create_assistant(
        name="a",
        model="m",
        instructions="i",
        greeting="g",
        voice="v",
        insight_group_id=group_id,
        dynamic_variables={},
    )
    await gw.delete_assistant(assistant_id=assistant.id)
    with pytest.raises(TelnyxNotFound):
        await gw.get_assistant(assistant_id=assistant.id)
    # The TeXML app survives and needs its own delete.
    await gw.delete_texml_application(texml_app_id=assistant.default_texml_app_id)
    with pytest.raises(TelnyxNotFound):
        await gw.delete_texml_application(texml_app_id=assistant.default_texml_app_id)
