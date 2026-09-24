import json

import omni.kit.test
import wandelbots_api_client.v2.models as wb_v2_models

from wandelbots.omni.core.payload.payload_store import (
    PAYLOAD_KEY_PREFIX,
    list_payload_names,
    load_payload,
    payload_key,
    payload_name_from_key,
    store_payload,
)


class FakeStoreObjectApi:
    """The three StoreObjectApi calls the payload store uses, on a dict."""

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects: dict[str, bytes] = dict(objects or {})
        self.stored_cells: list[str] = []

    async def store_object(self, cell: str, key: str, any_value: bytes) -> None:
        self.stored_cells.append(cell)
        self.objects[key] = any_value

    async def list_all_object_keys(self, cell: str) -> list[str]:
        return list(self.objects)

    async def get_object(self, cell: str, key: str) -> bytearray:
        return bytearray(self.objects[key])


def gripper_payload() -> wb_v2_models.Payload:
    return wb_v2_models.Payload(
        name="gripper",
        payload=2.5,
        center_of_mass=[1.0, -2.0, 150.0],
        moment_of_inertia=[0.01, 0.02, 0.03],
    )


class TestPayloadStore(omni.kit.test.AsyncTestCase):
    async def test_key_round_trip(self):
        self.assertEqual(f"{PAYLOAD_KEY_PREFIX}gripper", payload_key("gripper"))
        self.assertEqual("gripper", payload_name_from_key(payload_key("gripper")))
        self.assertIsNone(payload_name_from_key("trajectory-plan/gripper"))
        self.assertIsNone(payload_name_from_key(PAYLOAD_KEY_PREFIX))

    async def test_store_writes_the_planner_json_under_the_payload_key(self):
        store_api = FakeStoreObjectApi()

        await store_payload(store_api, "cell", gripper_payload())

        self.assertEqual(["cell"], store_api.stored_cells)
        stored = json.loads(store_api.objects["payload/gripper"].decode("utf-8"))
        self.assertEqual(
            {
                "name": "gripper",
                "payload": 2.5,
                "center_of_mass": [1.0, -2.0, 150.0],
                "moment_of_inertia": [0.01, 0.02, 0.03],
            },
            stored,
        )

    async def test_list_returns_only_payload_names_sorted(self):
        store_api = FakeStoreObjectApi(
            {
                "payload/vacuum": b"{}",
                "trajectory-plan/pick": b"{}",
                "payload/gripper": b"{}",
                "ghost-objects": b"{}",
            }
        )

        names = await list_payload_names(store_api, "cell")

        self.assertEqual(["gripper", "vacuum"], names)

    async def test_load_reads_back_what_was_stored(self):
        store_api = FakeStoreObjectApi()
        await store_payload(store_api, "cell", gripper_payload())

        loaded = await load_payload(store_api, "cell", "gripper")

        self.assertEqual(gripper_payload(), loaded)

    async def test_load_of_a_bare_payload_without_optional_fields(self):
        store_api = FakeStoreObjectApi(
            {"payload/plain": json.dumps({"name": "plain", "payload": 1.0}).encode()}
        )

        loaded = await load_payload(store_api, "cell", "plain")

        self.assertEqual("plain", loaded.name)
        self.assertIsNone(loaded.center_of_mass)
        self.assertIsNone(loaded.moment_of_inertia)
