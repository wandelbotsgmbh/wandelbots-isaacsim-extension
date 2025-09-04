# Wandelbots NOVA/Generate Range Select Node
import wandelbots.omni.ogn.utils.state_nodes as state_nodes


state_nodes.create_range_select_node(
    "/World/ActionGraph",
    [
        ("Range1", 0.5, state_nodes.RangeCompare.LESS_THAN),
        ("Range2", 1.0, state_nodes.RangeCompare.LESS_THAN_OR_EQUAL),
    ],
    "RangeSelectTest",
)
