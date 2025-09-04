# Wandelbots NOVA/Generate Truth Table Node
import wandelbots.omni.ogn.utils.state_nodes as state_nodes


state_nodes.create_truth_table_node(
    "/World/ActionGraph",
    state_nodes.TruthTable(
        ["A", "B", "C", "D"],
        {
            "StateA": [True, False, False, False],
            "StateB": [False, True, True, True],
        },
    ),
    "TestTruthTable",
)
