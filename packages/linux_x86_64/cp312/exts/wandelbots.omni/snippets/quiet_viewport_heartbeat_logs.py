# Wandelbots NOVA/Quiet Viewport Heartbeat Logs
# Kit's viewport heartbeat drives continuous redraw through the multitick render
# scheduler, which logs "Simulating 1 sensor(s) for step [...]" on every tick.
# Nothing this extension creates, but it floods the console over a long session.
# Run this to raise both channels' floor to warnings for the current session.
import carb.settings

settings = carb.settings.get_settings()

for channel in ("omni.usd.multitick.render", "omni.sensorscheduling"):
    settings.set(f"/log/channels/{channel}", "warning")
