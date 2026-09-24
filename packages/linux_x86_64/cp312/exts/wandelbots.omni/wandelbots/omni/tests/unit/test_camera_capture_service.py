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
    NoCameraAtPathError,
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
        # SceneUtils reads the stage from the usd context, so the unit has to
        # be set there rather than on an in-memory stage.
        await omni.usd.get_context().new_stage_async()
        UsdGeom.SetStageMetersPerUnit(omni.usd.get_context().get_stage(), 1.0)
        capture = np.array([[1.5, np.inf], [np.nan, -np.inf]], dtype=np.float32)
        with _capture_returning(capture):
            distance = await self.service.get_distance("/World/Camera", (2, 2))
        self.assertEqual(distance, [[1500.0, 0.0], [0.0, 0.0]])

    async def test_distance_scales_stage_units_to_millimetres(self):
        await omni.usd.get_context().new_stage_async()
        UsdGeom.SetStageMetersPerUnit(omni.usd.get_context().get_stage(), 0.01)
        capture = np.array([[1.0, 2.0]], dtype=np.float32)

        with _capture_returning(capture):
            distance = await self.service.get_distance("/World/Camera", (2, 1))

        # 1 cm is 10 mm
        self.assertEqual(distance, [[10.0, 20.0]])

    async def test_camera_intrinsics_scale_with_requested_resolution(self):
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)

        intrinsics = self.service.get_camera_intrinsics("/World/Camera", (1280, 720))

        self.assertAlmostEqual(intrinsics[0][0], 1280 * 24.0 / 20.955, places=3)
        # fy is fx: Isaac Sim renders square pixels, so it does not come from
        # the authored verticalAperture.
        self.assertAlmostEqual(intrinsics[1][1], 1280 * 24.0 / 20.955, places=3)
        self.assertEqual(intrinsics[0][2], 640.0)
        self.assertEqual(intrinsics[1][2], 360.0)
        self.assertEqual(intrinsics[2], [0.0, 0.0, 1.0])

    async def test_camera_intrinsics_halve_with_half_the_resolution(self):
        """The same camera rendered smaller has proportionally smaller fx/fy -
        this is what makes pairing intrinsics with the wrong resolution wrong."""
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)

        full = self.service.get_camera_intrinsics("/World/Camera", (1280, 720))
        half = self.service.get_camera_intrinsics("/World/Camera", (640, 360))

        self.assertAlmostEqual(full[0][0] / 2.0, half[0][0], places=6)
        self.assertAlmostEqual(full[1][1] / 2.0, half[1][1], places=6)

    async def test_pixels_stay_square_at_any_requested_aspect_ratio(self):
        """The authored verticalAperture belongs to the camera's own resolution.

        Pairing it with a different requested height made fy anamorphic: a ZED
        authored at 1920x1080 and asked for 1280x720 reported an fy that skewed
        every unprojection downstream. Isaac Sim renders square pixels, so fy
        follows fx whatever aspect ratio is asked for.
        """
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        # In sync with 1920x1080, the camera's native resolution.
        camera.GetVerticalApertureAttr().Set(20.955 * 1080.0 / 1920.0)

        for resolution in ((1920, 1080), (1280, 720), (640, 640), (800, 1200)):
            with self.subTest(resolution=resolution):
                intrinsics = self.service.get_camera_intrinsics(
                    "/World/Camera", resolution
                )

                self.assertAlmostEqual(intrinsics[0][0], intrinsics[1][1], places=6)

    async def test_an_out_of_sync_vertical_aperture_does_not_skew_fy(self):
        """The attribute can be left at anything; it must not reach the matrix."""
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(3.0)

        intrinsics = self.service.get_camera_intrinsics("/World/Camera", (1280, 720))

        self.assertAlmostEqual(intrinsics[0][0], intrinsics[1][1], places=6)

    async def test_get_depth_capture_combines_distance_and_intrinsics(self):
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)
        capture = np.array([[1.0, np.inf]], dtype=np.float32)

        with _capture_returning(capture):
            result = await self.service.get_depth_capture("/World/Camera", (2, 1))

        self.assertEqual(result.depth, [[1000.0, 0.0]])
        self.assertEqual("mm", result.unit)
        self.assertEqual(
            result.camera_intrinsics,
            self.service.get_camera_intrinsics("/World/Camera", (2, 1)),
        )

    async def test_depth_can_be_asked_for_in_metres(self):
        """The unit is a request parameter and comes back with the answer, so
        the next caller who wants metres needs no new contract."""
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)
        capture = np.array([[1.0, np.inf]], dtype=np.float32)

        with _capture_returning(capture):
            result = await self.service.get_depth_capture("/World/Camera", (2, 1), "m")

        self.assertEqual(result.depth, [[1.0, 0.0]])
        self.assertEqual("m", result.unit)

    async def test_the_unit_does_not_touch_the_intrinsics(self):
        """fx/fy/cx/cy are pixels; they must not move with the depth unit."""
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.SetStageMetersPerUnit(stage, 1.0)
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)
        capture = np.array([[1.0, 2.0]], dtype=np.float32)

        with _capture_returning(capture):
            in_millimetres = await self.service.get_depth_capture(
                "/World/Camera", (2, 1), "mm"
            )
        with _capture_returning(capture):
            in_metres = await self.service.get_depth_capture(
                "/World/Camera", (2, 1), "m"
            )

        self.assertEqual(in_millimetres.camera_intrinsics, in_metres.camera_intrinsics)

    async def test_intrinsics_are_a_three_by_three_matrix(self):
        """The K matrix is what the caller multiplies pixel coordinates with,
        so both dimensions are part of the contract, not just the row count."""
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)

        intrinsics = self.service.get_camera_intrinsics("/World/Camera", (640, 480))

        self.assertEqual(len(intrinsics), 3)
        for row in intrinsics:
            self.assertEqual(len(row), 3)

    async def test_intrinsics_on_a_prim_that_is_no_camera_raises(self):
        """A wrong path used to surface as a TypeError from the arithmetic,
        which said nothing about the path being wrong."""
        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World/NotACamera")

        with self.assertRaises(NoCameraAtPathError):
            self.service.get_camera_intrinsics("/World/NotACamera", (640, 480))

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

    @staticmethod
    def _a_captured_3d_box(extent, translation):
        """One annotator row, in the dtype replicator hands back."""
        dtype = np.dtype(
            [
                ("semanticId", "<u4"),
                ("x_min", "<f4"),
                ("y_min", "<f4"),
                ("z_min", "<f4"),
                ("x_max", "<f4"),
                ("y_max", "<f4"),
                ("z_max", "<f4"),
                ("transform", "<f4", (4, 4)),
                ("occlusionRatio", "<f4"),
            ]
        )
        transform = np.eye(4, dtype=np.float32)
        transform[3, :3] = translation
        row = (1, *extent, transform, 0.0)
        return {
            "data": np.array([row], dtype=dtype),
            "info": {
                "idToLabels": {"1": {"class": "robot"}},
                "primPaths": ["/World/robot"],
            },
        }

    async def test_a_3d_box_answers_in_millimetres_all_through(self):
        """The extent is a length like the translation, so it scales like one.

        Read from a UR10e whose world bounds USD puts at
        min (-1231.2, -290.7, 688.0) mm: leaving the extent in stage units
        while the translation is in millimetres puts a 1.3 m robot on the
        wire as a 1.3 mm one standing at 688 mm.
        """
        await omni.usd.get_context().new_stage_async()
        UsdGeom.SetStageMetersPerUnit(omni.usd.get_context().get_stage(), 1.0)
        capture = self._a_captured_3d_box(
            extent=(-1.231, -0.291, 0.0, 0.095, 0.095, 0.275),
            translation=(0.0, 0.0, 0.688),
        )

        with _capture_returning(capture):
            boxes = await self.service.get_bounding_boxes(
                "/World/Camera", (1, 1), "3D", ["robot"]
            )

        self.assertEqual(len(boxes), 1)
        for got, want in zip(boxes[0].bbox, (-1231.0, -291.0, 0.0, 95.0, 95.0, 275.0)):
            self.assertAlmostEqual(got, want, delta=1.0)
        self.assertAlmostEqual(boxes[0].transform[3][2], 688.0, delta=1.0)

    async def test_a_3d_box_scales_by_the_stage_unit_not_by_a_constant(self):
        """A centimetre stage scales by ten, so the factor cannot be hardcoded."""
        await omni.usd.get_context().new_stage_async()
        UsdGeom.SetStageMetersPerUnit(omni.usd.get_context().get_stage(), 0.01)
        capture = self._a_captured_3d_box(
            extent=(-1.0, -2.0, -3.0, 1.0, 2.0, 3.0), translation=(4.0, 0.0, 0.0)
        )

        with _capture_returning(capture):
            boxes = await self.service.get_bounding_boxes(
                "/World/Camera", (1, 1), "3D", ["robot"]
            )

        for got, want in zip(boxes[0].bbox, (-10.0, -20.0, -30.0, 10.0, 20.0, 30.0)):
            self.assertAlmostEqual(got, want, delta=0.01)
        self.assertAlmostEqual(boxes[0].transform[3][0], 40.0, delta=0.01)

    async def test_normals_json_replaces_non_finite_values_with_zero(self):
        """Same trap as depth: a camera looking past the scene broke the
        request, because a pixel without geometry carries NaN normals."""
        capture = np.array(
            [[[0.0, 0.0, 1.0], [np.nan, np.nan, np.nan]]], dtype=np.float32
        )
        with _capture_returning(capture):
            normals = await self.service.get_normals("/World/Camera", (2, 1))
        self.assertEqual(normals, [[[0.0, 0.0, 1.0], [0.0, 0.0, 0.0]]])

    async def test_pointcloud_json_replaces_non_finite_values_with_zero(self):
        """Only the non-finite component is zeroed, and no numpy warning fires.

        Sanitizing before the camera transform matters: a matmul turns one inf
        into NaN across the whole row (inf * 0 is NaN), which both loses the
        neighbouring components and prints a RuntimeWarning per capture.
        """
        await omni.usd.get_context().new_stage_async()
        UsdGeom.SetStageMetersPerUnit(omni.usd.get_context().get_stage(), 1.0)
        capture = {
            "data": np.array([[1.0, np.inf, 3.0], [1.0, 2.0, 3.0]]),
            "info": {
                "pointRgb": np.array([[255, 0, 0, 255], [0, 255, 0, 255]]),
                "pointNormals": np.array(
                    [[np.nan, 0.0, 1.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
                ),
            },
        }
        identity = mock.AsyncMock(return_value=np.eye(4))
        with _capture_returning(capture):
            with mock.patch.object(SyntheticDataUtils, "get_camera_tfm", identity):
                pointcloud = await self.service.get_pointcloud("/World/Camera", (2, 1))

        self.assertEqual(
            pointcloud.points, [[1000.0, 0.0, 3000.0], [1000.0, 2000.0, 3000.0]]
        )
        self.assertEqual(pointcloud.normals, [[0.0, 0.0, 1.0], [0.0, 0.0, 1.0]])


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


class TestDepthColourRamp(omni.kit.test.AsyncTestCase):
    """The PNG ramp fits the frame unless a range was asked for.

    near/far are stage units, so the old fixed 1e-5/100.0 rendered a
    millimetre stage almost entirely saturated and a metre stage almost
    entirely black.
    """

    def setUp(self):
        self.service = CameraCaptureService()

    async def test_an_unset_range_is_taken_from_the_frame(self):
        distance = np.array([[100.0, 200.0], [300.0, 400.0]])
        near, far = self.service._colour_ramp(distance, None, None)
        self.assertEqual((near, far), (100.0, 400.0))

    async def test_an_explicit_range_is_kept(self):
        distance = np.array([[100.0, 200.0]])
        self.assertEqual(
            self.service._colour_ramp(distance, 0.0, 1000.0), (0.0, 1000.0)
        )

    async def test_non_finite_pixels_do_not_stretch_the_ramp(self):
        distance = np.array([[100.0, np.inf], [np.nan, 400.0]])
        near, far = self.service._colour_ramp(distance, None, None)
        self.assertEqual((near, far), (100.0, 400.0))

    async def test_a_single_depth_frame_does_not_divide_by_zero(self):
        near, far = self.service._colour_ramp(np.array([[5.0, 5.0]]), None, None)
        self.assertGreater(far, near)

    async def test_an_all_non_finite_frame_still_yields_a_usable_ramp(self):
        near, far = self.service._colour_ramp(np.array([[np.inf, np.inf]]), None, None)
        self.assertGreater(far, near)


class TestColorize3DBoundingBoxes(omni.kit.test.AsyncTestCase):
    """Drawing the 3D wireframes onto a captured frame.

    The projection arithmetic is covered without Kit in
    test_project_box_corners; what needs a stage is the camera lookup and the
    USD matrices it feeds, which no other test exercises.
    """

    RESOLUTION = (200, 100)

    async def a_camera_looking_down_minus_z(self, path="/World/Camera"):
        from PIL import Image

        await omni.usd.get_context().new_stage_async()
        stage = omni.usd.get_context().get_stage()
        camera = UsdGeom.Camera.Define(stage, path)
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetHorizontalApertureAttr().Set(20.955)
        camera.GetVerticalApertureAttr().Set(11.787)
        return Image.new("RGB", self.RESOLUTION, (0, 0, 0))

    def a_box_in_front(self, distance_mm=5000.0):
        from wandelbots.omni.periphery.camera_configuration import BoundingBox3D

        transform = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, -distance_mm, 1.0],
        ]
        return BoundingBox3D(
            label="box",
            bbox=(-500.0, -500.0, -500.0, 500.0, 500.0, 500.0),
            prim_path="/World/box",
            semantic_id="0",
            transform=transform,
        )

    async def test_the_wireframe_reaches_the_frame(self):
        image = await self.a_camera_looking_down_minus_z()

        drawn = SyntheticDataUtils.colorize_3d_bounding_boxes(
            "/World/Camera", self.RESOLUTION, image, [self.a_box_in_front()]
        )

        self.assertIsNotNone(drawn.getbbox())

    async def test_a_path_without_a_camera_says_so(self):
        image = await self.a_camera_looking_down_minus_z()

        with self.assertRaises(NoCameraAtPathError):
            SyntheticDataUtils.colorize_3d_bounding_boxes(
                "/World/not_a_camera", self.RESOLUTION, image, [self.a_box_in_front()]
            )

    async def test_a_box_behind_the_camera_leaves_the_frame_untouched(self):
        image = await self.a_camera_looking_down_minus_z()
        behind = self.a_box_in_front(distance_mm=-5000.0)

        drawn = SyntheticDataUtils.colorize_3d_bounding_boxes(
            "/World/Camera", self.RESOLUTION, image, [behind]
        )

        self.assertIsNone(drawn.getbbox())
