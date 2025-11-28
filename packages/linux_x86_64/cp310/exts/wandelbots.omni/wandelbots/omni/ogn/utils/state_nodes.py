from dataclasses import dataclass
from enum import Enum
import omni.graph.core as og
import string
from itertools import product


@dataclass
class TruthTable:
    column_names: list[str]
    # states with their column values
    table: dict[str, list[bool]]


def create_truth_table_node(
    graph_path: str,
    truth_table: TruthTable,
    name="TruthTable",
    include_undefined_state=True,
):
    # bool nodes all n inputs which are named like a,b,c,d ... like the function _generate_letter_list generates
    bool_and_input_mapping = dict(
        zip(
            truth_table.column_names,
            _generate_letter_list(len(truth_table.column_names)),
        )
    )

    # Buffers just pass the input values. This is needed because the ogn api call fails if multiple targets for a compound input are used.
    compound_buffers = dict(
        [column, f"Buffer_{column}"] for column in truth_table.column_names
    )
    compound_outputs = dict([key, f"outputs:{key}"] for key in truth_table.table.keys())
    compound_connect = []
    compound_nodes: dict[str, str] = dict()
    compound_promote = []
    compound_and_attributes = []

    previous_state_branch_name = None
    for state_key, state_table in truth_table.table.items():
        # Connect state value AND logic
        and_name = f"And_{state_key}"
        compound_nodes[and_name] = "omni.graph.nodes.BooleanAnd"
        if len(truth_table.column_names) > 2:
            for input_name in list(bool_and_input_mapping.values())[2:]:
                compound_and_attributes.append(
                    (f"{and_name}.inputs:{input_name}", og.Type(og.BaseDataType.BOOL))
                )

        for value_name, value in zip(truth_table.column_names, state_table):
            and_input = f"{and_name}.inputs:{bool_and_input_mapping[value_name]}"
            buffer_name = compound_buffers[value_name]

            # Case value = 1
            if value:
                compound_connect.append((f"{buffer_name}.outputs:converted", and_input))
                continue

            # Case value = 0
            not_name = f"Not_{value_name}"
            if not_name not in compound_nodes:
                compound_nodes[not_name] = "omni.graph.nodes.BooleanNot"

            compound_connect += [
                (
                    f"{buffer_name}.outputs:converted",
                    f"{not_name}.inputs:valueIn",
                ),
                (f"{not_name}.outputs:valueOut", and_input),
            ]

        # exec output for state after AND result
        state_branch_name = f"Branch_{state_key}"
        compound_nodes[state_branch_name] = "omni.graph.action.Branch"
        compound_connect.append(
            (f"{and_name}.outputs:result", f"{state_branch_name}.inputs:condition")
        )
        compound_promote.append(
            (f"{state_branch_name}.outputs:execTrue", compound_outputs[state_key])
        )

        if previous_state_branch_name is None:
            compound_promote.append(
                (f"{state_branch_name}.inputs:execIn", "inputs:execIn")
            )
        else:
            compound_connect.append(
                (
                    f"{previous_state_branch_name}.outputs:execFalse",
                    f"{state_branch_name}.inputs:execIn",
                )
            )
        previous_state_branch_name = state_branch_name

    # Create buffers for the inputs
    for column_name, buffer_name in compound_buffers.items():
        compound_nodes[buffer_name] = "omni.graph.nodes.ToBool"
        compound_promote.append(
            (
                f"{buffer_name}.inputs:value",
                f"inputs:{column_name}",
            )
        )

    # Connect last branch false value to undefined state
    if include_undefined_state:
        compound_promote.append(
            (
                f"{previous_state_branch_name}.outputs:execFalse",
                "outputs:execUndefined",
            )
        )

    og.Controller().edit(
        graph_path,
        {
            og.Controller.Keys.CREATE_NODES: [
                (
                    name,
                    {
                        og.Controller.Keys.CREATE_NODES: [
                            (node_id, node_type)
                            for node_id, node_type in compound_nodes.items()
                        ],
                        og.Controller.Keys.PROMOTE_ATTRIBUTES: compound_promote,
                        og.Controller.Keys.CONNECT: compound_connect,
                        og.Controller.Keys.CREATE_ATTRIBUTES: compound_and_attributes,
                    },
                )
            ],
        },
    )


class RangeCompare(Enum):
    LESS_THAN = "<"
    LESS_THAN_OR_EQUAL = "<="


def create_range_select_node(
    graph_path: str,
    ranges: list[tuple[str, float, RangeCompare]] = [
        ("Range 1", 0.0, RangeCompare.LESS_THAN),
        ("Range 2", 1.0, RangeCompare.LESS_THAN_OR_EQUAL),
    ],
    name="TruthTable",
    include_out_of_range_state=True,
):
    input_name = "value"
    # Buffers just pass the input values. This is needed because the ogn api call fails if multiple targets for a compound input are used.
    buffer_name = f"Buffer_{input_name}"

    compound_outputs = dict([name, f"outputs:{name}"] for name, _, _ in ranges)
    compound_connect = []
    compound_nodes: dict[str, str] = dict()
    compound_promote = []
    compare_values = []

    previous_state_branch_name = None
    for range_name, range_compare_value, range_operation in ranges:
        # Connect state value COMPARE logic
        compare_name = f"Compare_{range_name}"
        compound_nodes[compare_name] = "omni.graph.nodes.Compare"

        compare_value_name = f"Compare_{range_name}_value"
        compound_nodes[compare_value_name] = "omni.graph.nodes.ConstantFloat"

        compare_values.append(
            (f"{compare_name}.inputs:operation", range_operation.value)
        )
        compare_values.append(
            (f"{compare_value_name}.inputs:value", range_compare_value)
        )

        # exec output for state after COMPARE result
        state_branch_name = f"Branch_{range_name}"
        compound_nodes[state_branch_name] = "omni.graph.action.Branch"
        compound_connect += [
            (
                f"{buffer_name}.outputs:converted",
                f"{compare_name}.inputs:a",
            ),
            (
                f"{compare_value_name}.inputs:value",
                f"{compare_name}.inputs:b",
            ),
            (
                f"{compare_name}.outputs:result",
                f"{state_branch_name}.inputs:condition",
            ),
        ]
        compound_promote.append(
            (f"{state_branch_name}.outputs:execTrue", compound_outputs[range_name])
        )

        if previous_state_branch_name is None:
            compound_promote.append(
                (f"{state_branch_name}.inputs:execIn", "inputs:execIn")
            )
        else:
            compound_connect.append(
                (
                    f"{previous_state_branch_name}.outputs:execFalse",
                    f"{state_branch_name}.inputs:execIn",
                )
            )
        previous_state_branch_name = state_branch_name

    # Create buffers for the inputs
    compound_nodes[buffer_name] = "omni.graph.nodes.ToBool"
    compound_promote.append(
        (
            f"{buffer_name}.inputs:value",
            f"inputs:{input_name}",
        )
    )

    # Connect last branch false value to out of range state
    if include_out_of_range_state:
        compound_promote.append(
            (
                f"{previous_state_branch_name}.outputs:execFalse",
                "outputs:OutOfRange",
            )
        )

    og.Controller().edit(
        graph_path,
        {
            og.Controller.Keys.CREATE_NODES: [
                (
                    name,
                    {
                        og.Controller.Keys.CREATE_NODES: [
                            (node_id, node_type)
                            for node_id, node_type in compound_nodes.items()
                        ],
                        og.Controller.Keys.PROMOTE_ATTRIBUTES: compound_promote,
                        og.Controller.Keys.CONNECT: compound_connect,
                        og.Controller.Keys.SET_VALUES: compare_values,
                    },
                )
            ],
        },
    )


def _generate_letter_list(n):
    letters = string.ascii_lowercase
    result = []
    length = 1

    while len(result) < n:
        for comb in product(letters, repeat=length):
            result.append("".join(comb))
            if len(result) == n:
                break
        length += 1

    return result
