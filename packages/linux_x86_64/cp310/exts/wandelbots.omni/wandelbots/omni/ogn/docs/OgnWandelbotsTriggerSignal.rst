.. _WandelbotsOmniExtension_OgnWandelbotsTriggerSignal_1:

.. _WandelbotsOmniExtension_OgnWandelbotsTriggerSignal:

.. ================================================================================
.. THIS PAGE IS AUTO-GENERATED. DO NOT MANUALLY EDIT.
.. ================================================================================

:orphan:

.. meta::
    :title: Trigger Signal
    :keywords: lang-en omnigraph node wandelbotsomniextension ogn-wandelbots-trigger-signal


Trigger Signal
==============

.. <description>

Trigger a Signal on a Wandelbots NOVA controller.

.. </description>


Installation
------------

To use this node enable :ref:`wandelbots.omni<ext_wandelbots_omni_nodes>` in the Extension Manager.


Inputs
------
.. csv-table::
    :header: "Name", "Type", "Descripton", "Default"
    :widths: 20, 20, 50, 10

    "Cell Identifier (*inputs:cell*)", "``string``", "Identifier of your cell within your Wandelbots NOVA instance", "cell"
    "Controller Identifier (*inputs:controller*)", "``string``", "Identifier of the controller within your Wandelbots NOVA instance", ""
    "Exec In (*inputs:exec_in*)", "``execution``", "Execution Input", "0"


Outputs
-------
.. csv-table::
    :header: "Name", "Type", "Descripton", "Default"
    :widths: 20, 20, 50, 10

    "Exec Out (*outputs:exec_out*)", "``execution``", "", "None"


Metadata
--------
.. csv-table::
    :header: "Name", "Value"
    :widths: 30,70

    "Unique ID", "WandelbotsOmniExtension.OgnWandelbotsTriggerSignal"
    "Version", "1"
    "Extension", "wandelbots.omni"
    "Has State?", "False"
    "Implementation Language", "Python"
    "Default Memory Type", "cpu"
    "Generated Code Exclusions", "None"
    "uiName", "Trigger Signal"
    "Wandelbots", "Wandelbots NOVA Configuration"
    "Generated Class Name", "OgnWandelbotsTriggerSignalDatabase"
    "Python Module", "wandelbots.omni"

