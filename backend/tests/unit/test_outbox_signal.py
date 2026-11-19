import json

from app.workers.outbox_relay import channels_for, signal_for

TENANT = "11111111-1111-4111-8111-111111111111"
CONN = "22222222-2222-4222-8222-222222222222"
CONV = "33333333-3333-4333-8333-333333333333"


def test_message_event_goes_to_tenant_and_visitor_channels():
    payload = {"tenant_id": TENANT, "connection_id": CONN, "visitor_id": "v1"}
    assert channels_for(payload) == [f"evt:t:{TENANT}", f"evt:v:{CONN}:v1"]


def test_event_without_visitor_goes_to_tenant_only():
    assert channels_for({"tenant_id": TENANT}) == [f"evt:t:{TENANT}"]


def test_signal_carries_ids_only():
    payload = {
        "tenant_id": TENANT,
        "conversation_id": CONV,
        "sequence": 7,
        "message_id": "m",
        "visitor_id": "v1",
    }
    signal = json.loads(signal_for(42, "message.created", payload))
    assert signal == {"id": 42, "t": "message.created", "c": CONV, "s": 7}


def test_signal_without_sequence():
    signal = json.loads(signal_for(1, "conversation.created", {"conversation_id": CONV}))
    assert signal == {"id": 1, "t": "conversation.created", "c": CONV, "s": None}
