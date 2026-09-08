"""Unit tests for the camera capture service data handling.

The replicator capture itself needs a renderer, so these tests patch
_capture_synthetic_data (or the replicator module) and cover the data
handling around it: non-finite depth sanitizing, the no-labels point cloud
guard, stage-unit scaling, and the pending-capture retry with cleanup.
"""

from unittest import mock

import numpy as np
import omni.kit.test
import omni.usd
from pxr import UsdGeom

import wandelbots.omni.periphery.camera_capture_service as capture_service_module
from wandelbots.omni.periphery.camera_capture_service import (
    CameraCaptureService,
    NoLabeledObjectsError,
)
from wandelbots.omni.utils.synthetic_data import SyntheticDataUtils


def _capture_returning(data):
    return mock.patch.object(
        CameraCaptureService,
        "_capture_synthetic_data",
        mock.AsyncMock(return_value=data),
    )


class TestCameraCaptureDataHandling(omni.kit.test.AsyncTestCase):
    def setUp(self):
        self.service = CameraCaptureService()

    async def test_distance_json_replaces_non_finite_values_with_zero(self):
        capture = np.array([[1.5, np.inf], [np.nan, -np.inf]], dtype=np.float32)
        with _capture_returning(capture):
            distance = await self.service.get_distance("/World/Camera", (2, 2))
        self.assertEqual(distance, [[1.5, 0.0], [0.0, 0.0]])

    async def test_pointcloud_without_labeled_objects_raises(self):
        with _capture_returning({"data": np.empty(0), "info": {}}):
            with self.assertRaises(NoLabeledObjectsError):
                await self.service.get_pointcloud("/World/Camera", (2, 2))

    async def test_pointcloud_scales_stage_units_to_millimeters(self):
        # SceneUtils reads the usd context stage, so the unit has to be set
        # there rather than on an in-memory stage.
        await omni.usd.get_context().new_stage_async()
        UsdGeom.SetStageMetersPerUnit(
            omni.usd.get_context().get_stage(), 0.01
        )  # centimeter stage
        capture = {
            "data": np.array([[1.0, 2.0, 3.0]]),
            "info": {
                "pointRgb": np.array([[255, 0, 0, 255]]),
                "pointNormals": np.array([[0.0, 0.0, 1.0, 0.0]]),
            },
        }
        identity = mock.AsyncMock(return_value=np.eye(4))
        with _capture_returning(capture):
            with mock.patch.object(SyntheticDataUtils, "get_camera_tfm", identity):
                pointcloud = await self.service.get_pointcloud("/World/Camera", (1, 1))
        # 1 cm = 10 mm
        self.assertEqual(pointcloud.points, [[10.0, 20.0, 30.0]])


class TestCameraCaptureRetry(omni.kit.test.AsyncTestCase):
    async def test_capture_retries_until_annotator_has_data(self):
        annotator = mock.Mock()
        annotator.get_data.side_effect = [np.empty(0), np.ones((2, 2))]
        render_product = mock.Mock()
        replicator = mock.Mock()
        replicator.create.render_product.return_value = render_product
        replicator.AnnotatorRegistry.get_annotator.return_value = annotator
        replicator.orchestrator.step_async = mock.AsyncMock()

        with mock.patch.object(capture_service_module, "rep", replicator):
            with mock.patch.object(
                capture_service_module.SceneUtils,
                "check_simulation",
                return_value=(None, False),
            ):
                data = await CameraCaptureService()._capture_synthetic_data(
                    "/World/Camera", "distance_to_camera", (2, 2)
                )

        self.assertEqual(data.shape, (2, 2))
        self.assertEqual(replicator.orchestrator.step_async.await_count, 2)
        annotator.detach.assert_called_once_with(render_product)
        render_product.destroy.assert_called_once()
