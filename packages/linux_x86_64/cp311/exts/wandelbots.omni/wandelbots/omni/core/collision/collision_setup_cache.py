import carb
from wandelbots.omni.utils.api import get_api_client_from_config, ApiConfiguration
import wandelbots_api_client.v2 as wb
from pxr import Usd
from wandelbots.omni.manipulators import (
    get_motion_group_configuration_from_prim,
)


class CollisionSetupCache:
    def __init__(self, cell: str, api_configuration: ApiConfiguration) -> None:
        self._cell = cell
        self._api_configuration = api_configuration
        self._cache: dict[str, wb.models.CollisionSetup] = dict()

    async def get(
        self, setup_name: str, force_refresh: bool = False
    ) -> wb.models.CollisionSetup | None:
        setup = self._cache.get(setup_name, None)
        if setup and not force_refresh:
            return setup

        async with get_api_client_from_config(self._api_configuration) as api_client:
            collision_setups_api = wb.StoreCollisionSetupsApi(api_client)
            try:
                collision_setup_keys = (
                    await collision_setups_api.list_stored_collision_setups_keys(
                        cell=self._cell
                    )
                )
                if setup_name not in collision_setup_keys:
                    # The setup is gone server-side; a stale cached copy must
                    # not outlive it.
                    self._cache.pop(setup_name, None)
                    carb.log_warn(f"Collision setup '{setup_name}' not found in store.")
                    return None

                setup = await collision_setups_api.get_stored_collision_setup(
                    cell=self._cell, setup=setup_name
                )
                self._cache[setup_name] = setup
                return setup
            except wb.ApiException as e:
                carb.log_warn(f"Failed to fetch collision setup '{setup_name}': {e}")
                return None


class PrimCollisionSetupCache:
    def __init__(self):
        self._cache: dict[str, CollisionSetupCache] = dict()

    async def get_by_cell(
        self,
        cell: str,
        api_configuration: ApiConfiguration,
        collision_setup_name: str,
        force_refresh: bool = False,
    ) -> wb.models.CollisionSetup | None:
        # Key by host and cell: instances share the default cell name "cell",
        # so the cell alone would serve setups from the wrong instance.
        cache_key = f"{api_configuration.host}/{cell}"
        collision_setup_cache = self._cache.get(cache_key)
        if collision_setup_cache is None:
            collision_setup_cache = CollisionSetupCache(cell, api_configuration)
            self._cache[cache_key] = collision_setup_cache

        return await collision_setup_cache.get(collision_setup_name, force_refresh)

    async def get(
        self, prim: Usd.Prim, collision_setup_name: str, force_refresh: bool = False
    ) -> wb.models.CollisionSetup | None:
        motion_group = get_motion_group_configuration_from_prim(prim)
        if not motion_group:
            carb.log_warn(
                f"Prim '{prim.GetPath().pathString}' is not part of a motion group, cannot fetch collision setup."
            )
            return None

        stream_config = motion_group.motion_stream_configuration
        return await self.get_by_cell(
            stream_config.cell,
            stream_config.get_api_configuration(),
            collision_setup_name,
            force_refresh,
        )
