r"""Support for simplified access to data on nodes of type wandelbots.omni.OgnReadIO

 __   ___ .  .  ___  __       ___  ___  __      __   __   __   ___
/ _` |__  |\ | |__  |__)  /\   |  |__  |  \    /  ` /  \ |  \ |__
\__| |___ | \| |___ |  \ /--\  |  |___ |__/    \__, \__/ |__/ |___

 __   __     .  .  __  ___     .  .  __   __     ___
|  \ /  \    |\ | /  \  |      |\/| /  \ |  \ | |__  \ /
|__/ \__/    | \| \__/  |      |  | \__/ |__/ | |     |

Read an IO value from a controller.
"""

import sys
import traceback
import usdrt

import omni.graph.core as og
import omni.graph.core._omni_graph_core as _og
import omni.graph.tools.ogn as ogn



class OgnReadIODatabase(og.Database):
    """Helper class providing simplified access to data on nodes of type wandelbots.omni.OgnReadIO

    Class Members:
        node: Node being evaluated

    Attribute Value Properties:
        Inputs:
            inputs.exec_in
            inputs.io_id
            inputs.robot
        Outputs:
            outputs.exec_out
            outputs.value_bool
            outputs.value_float
            outputs.value_int
    """

    # Imprint the generator and target ABI versions in the file for JIT generation
    GENERATOR_VERSION = (1, 79, 1)
    TARGET_VERSION = (2, 181, 8)

    # This is an internal object that provides per-class storage of a per-node data dictionary
    PER_NODE_DATA = {}

    # This is an internal object that describes unchanging attributes in a generic way
    # The values in this list are in no particular order, as a per-attribute tuple
    #     Name, Type, ExtendedTypeIndex, UiName, Description, Metadata,
    #     Is_Required, DefaultValue, Is_Deprecated, DeprecationMsg
    # You should not need to access any of this data directly, use the defined database interfaces
    INTERFACE = og.Database._get_interface([
        ('inputs:exec_in', 'execution', 0, None, 'Execution input', {ogn.MetadataKeys.DEFAULT: '0'}, True, 0, False, ''),
        ('inputs:io_id', 'string', 0, 'IO', 'Identifier of controller io', {ogn.MetadataKeys.DEFAULT: '""'}, True, "", False, ''),
        ('inputs:robot', 'target', 0, 'Robot', 'The root prim of the robot', {}, True, None, False, ''),
        ('outputs:exec_out', 'execution', 0, None, 'Execution out', {}, True, None, False, ''),
        ('outputs:value_bool', 'bool', 0, 'Boolean', 'Updated value if the IO consumes boolean values.', {}, True, None, False, ''),
        ('outputs:value_float', 'float', 0, 'Float', 'Updated value if the IO consumes float values.', {}, True, None, False, ''),
        ('outputs:value_int', 'int', 0, 'Integer', 'Updated value if the IO consumes integer values.', {}, True, None, False, ''),
    ])

    @classmethod
    def _populate_role_data(cls):
        """Populate a role structure with the non-default roles on this node type"""
        role_data = super()._populate_role_data()
        role_data.inputs.exec_in = og.AttributeRole.EXECUTION
        role_data.inputs.io_id = og.AttributeRole.TEXT
        role_data.inputs.robot = og.AttributeRole.TARGET
        role_data.outputs.exec_out = og.AttributeRole.EXECUTION
        return role_data

    class ValuesForInputs(og.DynamicAttributeAccess):
        LOCAL_PROPERTY_NAMES = {"exec_in", "io_id", "_setting_locked", "_batchedReadAttributes", "_batchedReadValues"}
        """Helper class that creates natural hierarchical access to input attributes"""
        def __init__(self, node: og.Node, attributes, dynamic_attributes: og.DynamicAttributeInterface):
            """Initialize simplified access for the attribute data"""
            context = node.get_graph().get_default_graph_context()
            super().__init__(context, node, attributes, dynamic_attributes)
            self._batchedReadAttributes = [self._attributes.exec_in, self._attributes.io_id]
            self._batchedReadValues = [0, ""]

        @property
        def robot(self):
            data_view = og.AttributeValueHelper(self._attributes.robot)
            return data_view.get()

        @robot.setter
        def robot(self, value):
            if self._setting_locked:
                raise og.ReadOnlyError(self._attributes.robot)
            data_view = og.AttributeValueHelper(self._attributes.robot)
            data_view.set(value)
            self.robot_size = data_view.get_array_size()

        @property
        def exec_in(self):
            return self._batchedReadValues[0]

        @exec_in.setter
        def exec_in(self, value):
            self._batchedReadValues[0] = value

        @property
        def io_id(self):
            return self._batchedReadValues[1]

        @io_id.setter
        def io_id(self, value):
            self._batchedReadValues[1] = value

        def __getattr__(self, item: str):
            if item in self.LOCAL_PROPERTY_NAMES:
                return object.__getattribute__(self, item)
            else:
                return super().__getattr__(item)

        def __setattr__(self, item: str, new_value):
            if item in self.LOCAL_PROPERTY_NAMES:
                object.__setattr__(self, item, new_value)
            else:
                super().__setattr__(item, new_value)

        def _prefetch(self):
            readAttributes = self._batchedReadAttributes
            newValues = _og._prefetch_input_attributes_data(readAttributes)
            if len(readAttributes) == len(newValues):
                self._batchedReadValues = newValues

    class ValuesForOutputs(og.DynamicAttributeAccess):
        LOCAL_PROPERTY_NAMES = {"exec_out", "value_bool", "value_float", "value_int", "_batchedWriteValues"}
        """Helper class that creates natural hierarchical access to output attributes"""
        def __init__(self, node: og.Node, attributes, dynamic_attributes: og.DynamicAttributeInterface):
            """Initialize simplified access for the attribute data"""
            context = node.get_graph().get_default_graph_context()
            super().__init__(context, node, attributes, dynamic_attributes)
            self._batchedWriteValues = { }

        @property
        def exec_out(self):
            value = self._batchedWriteValues.get(self._attributes.exec_out)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.exec_out)
                return data_view.get()

        @exec_out.setter
        def exec_out(self, value):
            self._batchedWriteValues[self._attributes.exec_out] = value

        @property
        def value_bool(self):
            value = self._batchedWriteValues.get(self._attributes.value_bool)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.value_bool)
                return data_view.get()

        @value_bool.setter
        def value_bool(self, value):
            self._batchedWriteValues[self._attributes.value_bool] = value

        @property
        def value_float(self):
            value = self._batchedWriteValues.get(self._attributes.value_float)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.value_float)
                return data_view.get()

        @value_float.setter
        def value_float(self, value):
            self._batchedWriteValues[self._attributes.value_float] = value

        @property
        def value_int(self):
            value = self._batchedWriteValues.get(self._attributes.value_int)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.value_int)
                return data_view.get()

        @value_int.setter
        def value_int(self, value):
            self._batchedWriteValues[self._attributes.value_int] = value

        def __getattr__(self, item: str):
            if item in self.LOCAL_PROPERTY_NAMES:
                return object.__getattribute__(self, item)
            else:
                return super().__getattr__(item)

        def __setattr__(self, item: str, new_value):
            if item in self.LOCAL_PROPERTY_NAMES:
                object.__setattr__(self, item, new_value)
            else:
                super().__setattr__(item, new_value)

        def _commit(self):
            _og._commit_output_attributes_data(self._batchedWriteValues)
            self._batchedWriteValues = { }

    class ValuesForState(og.DynamicAttributeAccess):
        """Helper class that creates natural hierarchical access to state attributes"""
        def __init__(self, node: og.Node, attributes, dynamic_attributes: og.DynamicAttributeInterface):
            """Initialize simplified access for the attribute data"""
            context = node.get_graph().get_default_graph_context()
            super().__init__(context, node, attributes, dynamic_attributes)

    def __init__(self, node):
        super().__init__(node)
        dynamic_attributes = self.dynamic_attribute_data(node, og.AttributePortType.ATTRIBUTE_PORT_TYPE_INPUT)
        self.inputs = OgnReadIODatabase.ValuesForInputs(node, self.attributes.inputs, dynamic_attributes)
        dynamic_attributes = self.dynamic_attribute_data(node, og.AttributePortType.ATTRIBUTE_PORT_TYPE_OUTPUT)
        self.outputs = OgnReadIODatabase.ValuesForOutputs(node, self.attributes.outputs, dynamic_attributes)
        dynamic_attributes = self.dynamic_attribute_data(node, og.AttributePortType.ATTRIBUTE_PORT_TYPE_STATE)
        self.state = OgnReadIODatabase.ValuesForState(node, self.attributes.state, dynamic_attributes)

    class abi:
        """Class defining the ABI interface for the node type"""

        @staticmethod
        def get_node_type():
            get_node_type_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'get_node_type', None)
            if callable(get_node_type_function):  # pragma: no cover
                return get_node_type_function()
            return 'wandelbots.omni.OgnReadIO'

        @staticmethod
        def compute(context, node):
            def database_valid():
                return True
            try:
                per_node_data = OgnReadIODatabase.PER_NODE_DATA[node.node_id()]
                db = per_node_data.get('_db')
                if db is None:
                    db = OgnReadIODatabase(node)
                    per_node_data['_db'] = db
                if not database_valid():
                    per_node_data['_db'] = None
                    return False
            except:
                db = OgnReadIODatabase(node)

            try:
                compute_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'compute', None)
                if callable(compute_function) and compute_function.__code__.co_argcount > 1:  # pragma: no cover
                    return compute_function(context, node)

                db.inputs._prefetch()
                db.inputs._setting_locked = True
                with og.in_compute():
                    return OgnReadIODatabase.NODE_TYPE_CLASS.compute(db)
            except Exception as error:  # pragma: no cover
                stack_trace = "".join(traceback.format_tb(sys.exc_info()[2].tb_next))
                db.log_error(f'Assertion raised in compute - {error}\n{stack_trace}', add_context=False)
            finally:
                db.inputs._setting_locked = False
                db.outputs._commit()
            return False

        @staticmethod
        def initialize(context, node):
            OgnReadIODatabase._initialize_per_node_data(node)
            initialize_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'initialize', None)
            if callable(initialize_function):  # pragma: no cover
                initialize_function(context, node)

            per_node_data = OgnReadIODatabase.PER_NODE_DATA[node.node_id()]

            def on_connection_or_disconnection(*args):
                per_node_data['_db'] = None

            node.register_on_connected_callback(on_connection_or_disconnection)
            node.register_on_disconnected_callback(on_connection_or_disconnection)

        @staticmethod
        def initialize_nodes(context, nodes):
            for n in nodes:
                OgnReadIODatabase.abi.initialize(context, n)

        @staticmethod
        def release(node):
            release_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'release', None)
            if callable(release_function):  # pragma: no cover
                release_function(node)
            OgnReadIODatabase._release_per_node_data(node)

        @staticmethod
        def init_instance(node, graph_instance_id):
            init_instance_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'init_instance', None)
            if callable(init_instance_function):  # pragma: no cover
                init_instance_function(node, graph_instance_id)

        @staticmethod
        def release_instance(node, graph_instance_id):
            release_instance_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'release_instance', None)
            if callable(release_instance_function):  # pragma: no cover
                release_instance_function(node, graph_instance_id)
            OgnReadIODatabase._release_per_node_instance_data(node, graph_instance_id)

        @staticmethod
        def update_node_version(context, node, old_version, new_version):
            update_node_version_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'update_node_version', None)
            if callable(update_node_version_function):  # pragma: no cover
                return update_node_version_function(context, node, old_version, new_version)
            return False

        @staticmethod
        def initialize_type(node_type):
            initialize_type_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'initialize_type', None)
            needs_initializing = True
            if callable(initialize_type_function):  # pragma: no cover
                needs_initializing = initialize_type_function(node_type)
            if needs_initializing:
                node_type.set_metadata(ogn.MetadataKeys.EXTENSION, "wandelbots.omni")
                node_type.set_metadata(ogn.MetadataKeys.UI_NAME, "Read IO")
                node_type.set_metadata(ogn.MetadataKeys.CATEGORIES, "Wandelbots NOVA")
                node_type.set_metadata(ogn.MetadataKeys.CATEGORY_DESCRIPTIONS, "Wandelbots NOVA,Wandelbots NOVA")
                node_type.set_metadata(ogn.MetadataKeys.DESCRIPTION, "Read an IO value from a controller.")
                node_type.set_metadata(ogn.MetadataKeys.LANGUAGE, "Python")
                OgnReadIODatabase.INTERFACE.add_to_node_type(node_type)

        @staticmethod
        def on_connection_type_resolve(node):
            on_connection_type_resolve_function = getattr(OgnReadIODatabase.NODE_TYPE_CLASS, 'on_connection_type_resolve', None)
            if callable(on_connection_type_resolve_function):  # pragma: no cover
                on_connection_type_resolve_function(node)

    NODE_TYPE_CLASS = None

    @staticmethod
    def register(node_type_class):
        OgnReadIODatabase.NODE_TYPE_CLASS = node_type_class
        og.register_node_type(OgnReadIODatabase.abi, 2)

    @staticmethod
    def deregister():
        og.deregister_node_type("wandelbots.omni.OgnReadIO")
