# Wandelbots NOVA/Console Filter Wandelbots
import carb.settings

settings = carb.settings.get_settings()

sources: dict = settings.get_settings_dictionary(
    "persistent/app/extensions/console/sources"
)

for key in list(sources.get_keys()):
    sources[key] = key.startswith("wandelbots.")
settings.set("persistent/app/extensions/console/sources", sources)
