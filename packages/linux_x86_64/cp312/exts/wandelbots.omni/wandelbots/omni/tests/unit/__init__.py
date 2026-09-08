# The Kit test runner only discovers the classes re-exported here, so a test
# class missing from the imports or from __all__ never runs.
from .test_prim_utils import TestPrimUtils
from .test_math_utils import TestMathUtils
from .test_stage_discovery import (
    TestStageDiscovery,
    TestStageDiscoveryHostNormalization,
    TestStalePrimPaths,
)
from .test_authored_geometry import TestExpandColliderPrims
from .test_connection_ownership import TestNormalizeHost, TestOwnsConnection
from .test_collider_shells import TestColliderShells
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
from .test_motion_stream_connector import (
    TestPendingJointCoalescing,
    TestApplyJointsDedupe,
    TestExternalStreamPhysicsFeedback,
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
    TestPlanTrajectory,
    TestPlanTrajectorySegments,
)
from .test_trajectory_planner_execution_service import TestExecutionService
from .test_execution_orchestrator import TestExecutionOrchestrator
from .test_planning_orchestrator import TestPlanningOrchestrator
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
from .test_semantic_labels import TestSemanticLabels


__all__ = [
    "TestPrimUtils",
    "TestMathUtils",
    "TestStageDiscovery",
    "TestStageDiscoveryHostNormalization",
    "TestStalePrimPaths",
    "TestNormalizeHost",
    "TestOwnsConnection",
    "TestExpandColliderPrims",
    "TestColliderShells",
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
    "TestPlanTrajectory",
    "TestPlanTrajectorySegments",
    "TestExecutionService",
    "TestExecutionOrchestrator",
    "TestPlanningOrchestrator",
    "TestCameraCaptureDataHandling",
    "TestCameraCaptureRetry",
    "TestIKManager",
    "TestAddNucleusServerWiring",
    "TestNucleusServerRoundTrip",
    "TestPoseItem",
    "TestPoseModel",
    "TestSemanticLabels",
]
