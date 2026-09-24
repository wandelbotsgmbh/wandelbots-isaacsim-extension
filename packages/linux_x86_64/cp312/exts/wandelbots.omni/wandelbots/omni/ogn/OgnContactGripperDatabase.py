r"""Support for simplified access to data on nodes of type wandelbots.omni.OgnContactGripper

GENERATED CODE. DO NOT MODIFY.

Attach prims overlapping a helper volume and keep them snapped to it. A prim overlaps when geometry at or below it touches
the world bounds of the sensor. With Fix true the node attaches the first overlapping candidate once. With Fix All it attaches
every overlapping candidate on each execution, so prims that enter the volume later follow as well. Trigger with Fix false
to release all of them.
"""

import numpy
import sys
import traceback
import usdrt

import omni.graph.core as og
_og = og._omni_graph_core
import omni.graph.tools.ogn as ogn




class OgnContactGripperDatabase(og.Database):
    """Helper class providing simplified access to data on nodes of type wandelbots.omni.OgnContactGripper

    Class Members:
        node: Node being evaluated

    Attribute Value Properties:
        Inputs:
            inputs.candidatePrimPaths
            inputs.excludePrimPaths
            inputs.execIn
            inputs.fixAll
            inputs.helperPrim
            inputs.stick
        Outputs:
            outputs.attachedPrimPath
            outputs.attachedPrimPaths
            outputs.execAttached
            outputs.execReleased
            outputs.isAttached
    """

    # Imprint the generator and target ABI versions in the file for JIT generation
    GENERATOR_VERSION = (1, 81, 0)
    TARGET_VERSION = (3, 1, 2)

    # This is an internal object that provides per-class storage of a per-node data dictionary
    PER_NODE_DATA = {}

    # This is an internal object that describes unchanging attributes in a generic way
    # The values in this list are in no particular order, as a per-attribute tuple
    #     Name, Type, ExtendedTypeIndex, UiName, Description, Metadata,
    #     Is_Required, DefaultValue, Is_Deprecated, DeprecationMsg
    # You should not need to access any of this data directly, use the defined database interfaces
    INTERFACE = og.Database._get_interface([
        ('inputs:candidatePrimPaths', 'token[]', 0, 'Candidate Prim Paths', 'Optional list of prim paths or wildcard filters that may be attached. A filter matches the whole path with shell wildcards, and * also crosses /, so /World/Boxes/* matches everything below /World/Boxes. A filter without a leading / matches at any depth, for example pallet-with-boxes/*. A matching group prim is attached as a whole, so let the filter name the part prims. If empty, the whole stage is scanned.', {ogn.MetadataKeys.DEFAULT: '[]'}, True, [], False, ''),
        ('inputs:excludePrimPaths', 'token[]', 0, 'Exclude Prim Paths', 'Optional list of prim paths or wildcard filters that must never be attached, for example /World/Robot or gripper/*. An excluded prim excludes everything below it. Exclusions win over candidate filters.', {ogn.MetadataKeys.DEFAULT: '[]'}, True, [], False, ''),
        ('inputs:execIn', 'execution', 0, None, 'Trigger once to evaluate the stick input. The node keeps snapping internally until stick becomes false.', {ogn.MetadataKeys.DEFAULT: '0'}, True, 0, False, ''),
        ('inputs:fixAll', 'bool', 0, 'Fix All', 'Attach every candidate prim overlapping the sensor instead of only the first, and repeat the scan on every execution while Fix is true. Each execution scans the stage, so in a large stage drive execIn from an event rather than from every frame.', {ogn.MetadataKeys.DEFAULT: 'false'}, True, False, False, ''),
        ('inputs:helperPrim', 'target', 0, 'Sensor Prim', 'Prim whose world-space bounds define the sticky volume.', {}, True, None, False, ''),
        ('inputs:stick', 'bool', 0, 'Fix', 'Hold true to attach and keep holding the overlapping prims. Set false to release them.', {ogn.MetadataKeys.DEFAULT: 'false'}, True, False, False, ''),
        ('outputs:attachedPrimPath', 'token', 0, 'Attached Prim Path', 'Path of the most recently attached prim, empty when nothing is attached.', {ogn.MetadataKeys.DEFAULT: '""'}, True, "", False, ''),
        ('outputs:attachedPrimPaths', 'token[]', 0, 'Attached Prim Paths', 'Paths of all currently attached prims, in the order they were attached.', {ogn.MetadataKeys.DEFAULT: '[]'}, True, [], False, ''),
        ('outputs:execAttached', 'execution', 0, None, 'Execution trigger output fired when at least one new prim is attached.', {}, True, None, False, ''),
        ('outputs:execReleased', 'execution', 0, None, 'Execution trigger output fired when the attached prims are released.', {}, True, None, False, ''),
        ('outputs:isAttached', 'bool', 0, 'Is Attached', 'True when at least one prim is currently attached.', {ogn.MetadataKeys.DEFAULT: 'false'}, True, False, False, ''),
    ])

    @classmethod
    def _populate_role_data(cls):
        """Populate a role structure with the non-default roles on this node type"""
        role_data = super()._populate_role_data()
        role_data.inputs.execIn = og.AttributeRole.EXECUTION
        role_data.inputs.helperPrim = og.AttributeRole.TARGET
        role_data.outputs.execAttached = og.AttributeRole.EXECUTION
        role_data.outputs.execReleased = og.AttributeRole.EXECUTION
        return role_data

    class ValuesForInputs(og.DynamicAttributeAccess):
        LOCAL_PROPERTY_NAMES = {"execIn", "fixAll", "stick", "_setting_locked", "_batchedReadAttributes", "_batchedReadValues"}
        """Helper class that creates natural hierarchical access to input attributes"""
        def __init__(self, node: og.Node, attributes, dynamic_attributes: og.DynamicAttributeInterface):
            """Initialize simplified access for the attribute data"""
            context = node.get_graph().get_default_graph_context()
            super().__init__(context, node, attributes, dynamic_attributes)
            self._batchedReadAttributes = [self._attributes.execIn, self._attributes.fixAll, self._attributes.stick]
            self._batchedReadValues = [0, False, False]

        @property
        def candidatePrimPaths(self):
            data_view = og.AttributeValueHelper(self._attributes.candidatePrimPaths)
            return data_view.get()

        @candidatePrimPaths.setter
        def candidatePrimPaths(self, value):
            if self._setting_locked:
                raise og.ReadOnlyError(self._attributes.candidatePrimPaths)
            data_view = og.AttributeValueHelper(self._attributes.candidatePrimPaths)
            data_view.set(value)
            self.candidatePrimPaths_size = data_view.get_array_size()

        @property
        def excludePrimPaths(self):
            data_view = og.AttributeValueHelper(self._attributes.excludePrimPaths)
            return data_view.get()

        @excludePrimPaths.setter
        def excludePrimPaths(self, value):
            if self._setting_locked:
                raise og.ReadOnlyError(self._attributes.excludePrimPaths)
            data_view = og.AttributeValueHelper(self._attributes.excludePrimPaths)
            data_view.set(value)
            self.excludePrimPaths_size = data_view.get_array_size()

        @property
        def helperPrim(self):
            data_view = og.AttributeValueHelper(self._attributes.helperPrim)
            return data_view.get()

        @helperPrim.setter
        def helperPrim(self, value):
            if self._setting_locked:
                raise og.ReadOnlyError(self._attributes.helperPrim)
            data_view = og.AttributeValueHelper(self._attributes.helperPrim)
            data_view.set(value)
            self.helperPrim_size = data_view.get_array_size()

        @property
        def execIn(self):
            return self._batchedReadValues[0]

        @execIn.setter
        def execIn(self, value):
            self._batchedReadValues[0] = value

        @property
        def fixAll(self):
            return self._batchedReadValues[1]

        @fixAll.setter
        def fixAll(self, value):
            self._batchedReadValues[1] = value

        @property
        def stick(self):
            return self._batchedReadValues[2]

        @stick.setter
        def stick(self, value):
            self._batchedReadValues[2] = value

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
        LOCAL_PROPERTY_NAMES = {"attachedPrimPath", "execAttached", "execReleased", "isAttached", "_batchedWriteValues"}
        """Helper class that creates natural hierarchical access to output attributes"""
        def __init__(self, node: og.Node, attributes, dynamic_attributes: og.DynamicAttributeInterface):
            """Initialize simplified access for the attribute data"""
            context = node.get_graph().get_default_graph_context()
            super().__init__(context, node, attributes, dynamic_attributes)
            self.attachedPrimPaths_size = 0
            self._batchedWriteValues = { }

        @property
        def attachedPrimPaths(self):
            data_view = og.AttributeValueHelper(self._attributes.attachedPrimPaths)
            return data_view.get(reserved_element_count=self.attachedPrimPaths_size)

        @attachedPrimPaths.setter
        def attachedPrimPaths(self, value):
            data_view = og.AttributeValueHelper(self._attributes.attachedPrimPaths)
            data_view.set(value)
            self.attachedPrimPaths_size = data_view.get_array_size()

        @property
        def attachedPrimPath(self):
            value = self._batchedWriteValues.get(self._attributes.attachedPrimPath)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.attachedPrimPath)
                return data_view.get()

        @attachedPrimPath.setter
        def attachedPrimPath(self, value):
            self._batchedWriteValues[self._attributes.attachedPrimPath] = value

        @property
        def execAttached(self):
            value = self._batchedWriteValues.get(self._attributes.execAttached)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.execAttached)
                return data_view.get()

        @execAttached.setter
        def execAttached(self, value):
            self._batchedWriteValues[self._attributes.execAttached] = value

        @property
        def execReleased(self):
            value = self._batchedWriteValues.get(self._attributes.execReleased)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.execReleased)
                return data_view.get()

        @execReleased.setter
        def execReleased(self, value):
            self._batchedWriteValues[self._attributes.execReleased] = value

        @property
        def isAttached(self):
            value = self._batchedWriteValues.get(self._attributes.isAttached)
            if value:
                return value
            else:
                data_view = og.AttributeValueHelper(self._attributes.isAttached)
                return data_view.get()

        @isAttached.setter
        def isAttached(self, value):
            self._batchedWriteValues[self._attributes.isAttached] = value

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
        self.inputs = OgnContactGripperDatabase.ValuesForInputs(node, self.attributes.inputs, dynamic_attributes)
        dynamic_attributes = self.dynamic_attribute_data(node, og.AttributePortType.ATTRIBUTE_PORT_TYPE_OUTPUT)
        self.outputs = OgnContactGripperDatabase.ValuesForOutputs(node, self.attributes.outputs, dynamic_attributes)
        dynamic_attributes = self.dynamic_attribute_data(node, og.AttributePortType.ATTRIBUTE_PORT_TYPE_STATE)
        self.state = OgnContactGripperDatabase.ValuesForState(node, self.attributes.state, dynamic_attributes)

    class abi:
        """Class defining the ABI interface for the node type"""

        @staticmethod
        def get_node_type():
            get_node_type_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'get_node_type', None)
            if callable(get_node_type_function):  # pragma: no cover
                return get_node_type_function()
            return 'wandelbots.omni.OgnContactGripper'

        @staticmethod
        def compute(context, node):
            def database_valid():
                return True
            try:
                per_node_data = OgnContactGripperDatabase.PER_NODE_DATA[node.node_id()]
                db = per_node_data.get('_db')
                if db is None:
                    db = OgnContactGripperDatabase(node)
                    per_node_data['_db'] = db
                if not database_valid():
                    per_node_data['_db'] = None
                    return False
            except:
                db = OgnContactGripperDatabase(node)

            try:
                compute_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'compute', None)
                if callable(compute_function) and compute_function.__code__.co_argcount > 1:  # pragma: no cover
                    return compute_function(context, node)

                db.inputs._prefetch()
                db.inputs._setting_locked = True
                with og.in_compute():
                    return OgnContactGripperDatabase.NODE_TYPE_CLASS.compute(db)
            except Exception as error:  # pragma: no cover
                stack_trace = "".join(traceback.format_tb(sys.exc_info()[2].tb_next))
                db.log_error(f'Assertion raised in compute - {error}\n{stack_trace}', add_context=False)
            finally:
                db.inputs._setting_locked = False
                db.outputs._commit()
            return False

        @staticmethod
        def initialize(context, node):
            OgnContactGripperDatabase._initialize_per_node_data(node)
            initialize_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'initialize', None)
            if callable(initialize_function):  # pragma: no cover
                initialize_function(context, node)

            per_node_data = OgnContactGripperDatabase.PER_NODE_DATA[node.node_id()]

            def on_connection_or_disconnection(*args):
                per_node_data['_db'] = None

            node.register_on_connected_callback(on_connection_or_disconnection)
            node.register_on_disconnected_callback(on_connection_or_disconnection)

        @staticmethod
        def initialize_nodes(context, nodes):
            for n in nodes:
                OgnContactGripperDatabase.abi.initialize(context, n)

        @staticmethod
        def release(node):
            release_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'release', None)
            if callable(release_function):  # pragma: no cover
                release_function(node)
            OgnContactGripperDatabase._release_per_node_data(node)

        @staticmethod
        def init_instance(node, graph_instance_id):
            init_instance_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'init_instance', None)
            if callable(init_instance_function):  # pragma: no cover
                init_instance_function(node, graph_instance_id)

        @staticmethod
        def release_instance(node, graph_instance_id):
            release_instance_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'release_instance', None)
            if callable(release_instance_function):  # pragma: no cover
                release_instance_function(node, graph_instance_id)
            OgnContactGripperDatabase._release_per_node_instance_data(node, graph_instance_id)

        @staticmethod
        def update_node_version(context, node, old_version, new_version):
            update_node_version_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'update_node_version', None)
            if callable(update_node_version_function):  # pragma: no cover
                return update_node_version_function(context, node, old_version, new_version)
            return False

        @staticmethod
        def initialize_type(node_type):
            initialize_type_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'initialize_type', None)
            needs_initializing = True
            if callable(initialize_type_function):  # pragma: no cover
                needs_initializing = initialize_type_function(node_type)
            if needs_initializing:
                node_type.set_metadata(ogn.MetadataKeys.EXTENSION, "wandelbots.omni")
                node_type.set_metadata(ogn.MetadataKeys.UI_NAME, "Contact Gripper")
                node_type.set_metadata(ogn.MetadataKeys.CATEGORIES, "Wandelbots NOVA")
                node_type.set_metadata(ogn.MetadataKeys.CATEGORY_DESCRIPTIONS, "Wandelbots NOVA,Wandelbots NOVA")
                node_type.set_metadata(ogn.MetadataKeys.DESCRIPTION, "Attach prims overlapping a helper volume and keep them snapped to it. A prim overlaps when geometry at or below it touches the world bounds of the sensor. With Fix true the node attaches the first overlapping candidate once. With Fix All it attaches every overlapping candidate on each execution, so prims that enter the volume later follow as well. Trigger with Fix false to release all of them.")
                node_type.set_metadata(ogn.MetadataKeys.LANGUAGE, "Python")
                OgnContactGripperDatabase.INTERFACE.add_to_node_type(node_type)
                node_type.set_has_state(True)

        @staticmethod
        def on_connection_type_resolve(node):
            on_connection_type_resolve_function = getattr(OgnContactGripperDatabase.NODE_TYPE_CLASS, 'on_connection_type_resolve', None)
            if callable(on_connection_type_resolve_function):  # pragma: no cover
                on_connection_type_resolve_function(node)

    NODE_TYPE_CLASS = None

    @staticmethod
    def register(node_type_class):
        OgnContactGripperDatabase.NODE_TYPE_CLASS = node_type_class
        og.register_node_type(OgnContactGripperDatabase.abi, 2)

    @staticmethod
    def deregister():
        og.deregister_node_type("wandelbots.omni.OgnContactGripper")
