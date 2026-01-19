# Wandelbots NOVA/Console Filter Wandelbots
import carb.settings

settings = carb.settings.get_settings()

sources: dict = settings.get_settings_dictionary(
    "persistent/app/extensions/console/sources"
)

filter_list = ["wandelbots.", "omni.kit.app", "omni.graph.core"]

for key in list(sources.get_keys()):
    sources[key] = any(key.startswith(prefix) for prefix in filter_list)
settings.set("persistent/app/extensions/console/sources", sources)
