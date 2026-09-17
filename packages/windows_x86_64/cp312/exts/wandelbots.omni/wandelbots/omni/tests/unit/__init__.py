# The Kit test runner only discovers the classes re-exported here, so a test
# class missing from the imports or from __all__ never runs.
from .test_articulation_root_relationship import TestAuthoredArticulationRoot
from .test_joint_index_order import (
    TestJointIndicesInMotionGroupOrder,
    TestJointPositionsInMotionGroupOrder,
)
from .test_prim_utils import TestPrimUtils
from .test_math_utils import TestMathUtils
from .test_stage_discovery import (
    TestStageDiscovery,
    TestStageDiscoveryHostNormalization,
    TestRobotPrimResolution,
    TestStalePrimPaths,
)
from .test_authored_geometry import TestExpandColliderPrims
from .test_connection_ownership import TestNormalizeHost, TestOwnsConnection
from .test_contact_gripper import TestContactGripperCandidateScan
from .test_collider_shells import TestColliderShells
from .test_collision_free_algorithm import TestCollisionFreeAlgorithm
from .test_collision_export_service import (
    TestIsStageAuthoredEquipment,
    TestLinkAttachmentReachability,
)
from .test_collision_utils import TestMergeLinkChainExtras
from .test_diagnose_service import TestCreateDiagnosePackageWithoutInstances
from .test_locations import (
    TestUrlScheme,
    TestNormalizeLocation,
    TestParentAndJoin,
)
from .test_motion_group_bool_model import TestMotionGroupBoolModelPathGuard
from .test_extension_shutdown import TestStopTimelineWithTheStreams
from .test_motion_stream_connector import (
    TestOnTimelineStop,
    TestWakeForReset,
    TestPendingJointCoalescing,
    TestApplyJointsDedupe,
    TestExternalStreamPhysicsFeedback,
    TestIdleSleepTargets,
)
from .test_nova_instance_compatibility import TestNovaInstanceCompatibility
from .test_trajectory_export_segmentation import (
    TestEffectiveTcp,
    TestGroupPosesByTcp,
    TestSegmentBlending,
    TestSegmentedExport,
)
from .test_trajectory_planner_models import TestTrajectoryPlannerModels
from .test_trajectory_planner_store import (
    TestTrajectoryPlannerStore,
    TestBlendingMigration,
)
from .test_trajectory_planner_helpers import (
    TestHostKey,
    TestExtractJointPositionLimits,
    TestBuildGlobalLimits,
    TestBuildMotionGroupSetup,
    TestFetchMotionGroupContext,
)
from .test_trajectory_planner_orchestrator_helpers import (
    TestDecimateIndices,
    TestSegmentSpeeds,
    TestSpeedsToColors,
    TestNovaConfigPayload,
)
from .test_reachability_service import TestCheckSingleModelCollisionGrouping
from .test_trajectory_planner_ik_service import TestIKService, TestIKResult
from .test_trajectory_planner_planning_service import (
    TestParsePlanTrajectoryError,
    TestFormatErrorFeedback,
    TestPlanResult,
    TestJointPositionFromFailedResponse,
    TestPlanFailureFromRaw,
    TestPlanTrajectory,
    TestPlanTrajectorySegments,
    TestPlanCollisionFree,
)
from .test_trajectory_planner_execution_service import TestExecutionService
from .test_execution_orchestrator import TestExecutionOrchestrator
from .test_planning_orchestrator import (
    TestPlanningOrchestrator,
    TestFailedPoseIndex,
    TestMarkPlanningFailure,
)
from .test_camera_capture_service import (
    TestCameraCaptureDataHandling,
    TestCameraCaptureRetry,
)
from .test_ik_manager import TestIKManager
from .test_nucleus_service import (
    TestAddNucleusServerWiring,
    TestNucleusServerRoundTrip,
)
from .test_pose_model import TestPoseItem, TestPoseModel
from .test_ghost_mesh_cache import (
    TestGhostMeshCache,
    TestGhostMeshRepair,
    TestGhostMeshBuild,
)
from .test_watertight_mesh import TestWatertightMesh
from .test_pose_gizmo import TestPoseGizmo
from .test_semantic_labels import TestSemanticLabels
from .test_prim_pose_stage_units import (
    TestColliderTransformStageUnits,
    TestGetPrimPoseStageUnits,
    TestPrimPoseRoundTripStageUnits,
    TestStageUnitScaleFactor,
)
from .test_mesh_merge_instancing import TestMergeInstancedToolMeshes
from .test_collapsible_section import TestCollapsibleSection
from .test_assigned_articulations import TestCollectAllGroups
from .test_virtual_controller_service import (
    TestPresetConfiguration,
    TestConnectMotionGroupWithRetry,
)
from .test_motion_group_retargeting import TestRetargetMotionGroupConfiguration
from .test_model_base_offsets import TestModelBaseOffsets
from .test_motion_group_geometry_check import (
    TestChainComparison,
    TestFlangeComparison,
    TestGeometryCheck,
    TestKinematicsMismatch,
    TestReadChain,
    TestReadFlangeFrame,
)
from .test_stage_resync_cache import (
    TestSceneMotionGroupPrimCacheResync,
    TestPoseXformOpFilter,
    TestArticulationCacheResync,
    TestMotionGroupArticulationResync,
)


__all__ = [
    "TestAuthoredArticulationRoot",
    "TestJointIndicesInMotionGroupOrder",
    "TestJointPositionsInMotionGroupOrder",
    "TestStopTimelineWithTheStreams",
    "TestPrimUtils",
    "TestCollapsibleSection",
    "TestCollectAllGroups",
    "TestPresetConfiguration",
    "TestConnectMotionGroupWithRetry",
    "TestRetargetMotionGroupConfiguration",
    "TestGetPrimPoseStageUnits",
    "TestPrimPoseRoundTripStageUnits",
    "TestColliderTransformStageUnits",
    "TestStageUnitScaleFactor",
    "TestMergeInstancedToolMeshes",
    "TestChainComparison",
    "TestFlangeComparison",
    "TestGeometryCheck",
    "TestKinematicsMismatch",
    "TestReadChain",
    "TestReadFlangeFrame",
    "TestMathUtils",
    "TestStageDiscovery",
    "TestStageDiscoveryHostNormalization",
    "TestRobotPrimResolution",
    "TestStalePrimPaths",
    "TestNormalizeHost",
    "TestOwnsConnection",
    "TestContactGripperCandidateScan",
    "TestExpandColliderPrims",
    "TestColliderShells",
    "TestCollisionFreeAlgorithm",
    "TestIsStageAuthoredEquipment",
    "TestLinkAttachmentReachability",
    "TestMergeLinkChainExtras",
    "TestCreateDiagnosePackageWithoutInstances",
    "TestUrlScheme",
    "TestNormalizeLocation",
    "TestParentAndJoin",
    "TestMotionGroupBoolModelPathGuard",
    "TestPendingJointCoalescing",
    "TestApplyJointsDedupe",
    "TestExternalStreamPhysicsFeedback",
    "TestIdleSleepTargets",
    "TestNovaInstanceCompatibility",
    "TestEffectiveTcp",
    "TestGroupPosesByTcp",
    "TestSegmentBlending",
    "TestSegmentedExport",
    "TestTrajectoryPlannerModels",
    "TestTrajectoryPlannerStore",
    "TestBlendingMigration",
    "TestHostKey",
    "TestExtractJointPositionLimits",
    "TestBuildGlobalLimits",
    "TestBuildMotionGroupSetup",
    "TestFetchMotionGroupContext",
    "TestDecimateIndices",
    "TestSegmentSpeeds",
    "TestSpeedsToColors",
    "TestNovaConfigPayload",
    "TestCheckSingleModelCollisionGrouping",
    "TestIKService",
    "TestIKResult",
    "TestParsePlanTrajectoryError",
    "TestFormatErrorFeedback",
    "TestPlanResult",
    "TestJointPositionFromFailedResponse",
    "TestPlanFailureFromRaw",
    "TestPlanTrajectory",
    "TestPlanTrajectorySegments",
    "TestPlanCollisionFree",
    "TestExecutionService",
    "TestExecutionOrchestrator",
    "TestPlanningOrchestrator",
    "TestFailedPoseIndex",
    "TestMarkPlanningFailure",
    "TestCameraCaptureDataHandling",
    "TestCameraCaptureRetry",
    "TestIKManager",
    "TestAddNucleusServerWiring",
    "TestNucleusServerRoundTrip",
    "TestPoseItem",
    "TestPoseModel",
    "TestGhostMeshCache",
    "TestGhostMeshRepair",
    "TestGhostMeshBuild",
    "TestOnTimelineStop",
    "TestWakeForReset",
    "TestWatertightMesh",
    "TestPoseGizmo",
    "TestSemanticLabels",
    "TestModelBaseOffsets",
    "TestSceneMotionGroupPrimCacheResync",
    "TestPoseXformOpFilter",
    "TestArticulationCacheResync",
    "TestMotionGroupArticulationResync",
]
