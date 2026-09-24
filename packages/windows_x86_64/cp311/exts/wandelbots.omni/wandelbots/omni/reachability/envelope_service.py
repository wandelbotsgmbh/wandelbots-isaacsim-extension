"""Reachability-envelope computation for the pose-teaching overlay.

One layer, in the arm's ``link_0`` (base) frame, positions in millimetres: the
voxels whose centre has a NOVA inverse-kinematics solution at a fixed TCP
orientation and offset, each graded by how far its joint solution stays from the
joint limits.

Candidates come from a regular grid over the reach sphere, refined in two passes
so a dense envelope stays affordable: a coarse pass decides where the fine pass
has to look, the fine pass decides what is drawn. Both passes are decided by
NOVA's batched ``inverse_kinematics`` endpoint - nothing here computes forward
kinematics locally, so what is drawn is what the robot's own solver accepts.

The grading is a comfort hint, not the verdict for a placed pose: a pose is
answered by its own inverse-kinematics solve (see the tool window), because the
grid only samples cell centres.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import aiohttp
import carb
import omni.kit.app
import numpy as np
import wandelbots_api_client.v2 as wb_v2
import wandelbots_api_client.v2.models as wb_v2_models
from wandelbots_api_client.v2.exceptions import OpenApiException
from pxr import Usd

from wandelbots.omni.reachability import dh_chain, warp_fk
from wandelbots.omni.reachability.ik_probe import (
    InverseKinematicsProbe,
    encode_ik_request,
)
from wandelbots.omni.utils.math import pose_to_matrix
from wandelbots.omni.utils.kinematics import base_pose_to_world

# Mid-range + base-joint-rotation seeds, mirroring utils/kinematics.py: the NOVA
# IK solver is local + single-seed, so we union a few seeds per batch.
_BASE_SEED_ANGLES = (0.0, -2.0, -1.0, 1.0, 2.0, 3.0)
_MAX_IK_BATCH = 2000
# NOVA answers a batch in one request, so the round trips are the cost. Eight in
# flight keeps a full grid moving without turning a density change into a burst
# of sixty simultaneous requests against the cell.
_MAX_CONCURRENT_IK_REQUESTS = 8
# Joint-space samples per sweep. The cost is one GPU kernel launch, not network
# round trips, so this can be large: it only has to be dense enough that the
# occupied voxels stop changing.
_WORKSPACE_SAMPLES = 200_000

# Shared by every envelope compute, so a density change and a snap search
# running at once still add up to eight requests, not sixteen.
_ik_request_slot = asyncio.Semaphore(_MAX_CONCURRENT_IK_REQUESTS)
# Held across one frame before a batch is encoded, so at most one batch is encoded
# per frame and a recompute spreads over frames instead of bunching into one.
_ik_frame_gate = asyncio.Lock()
# Below this a request is a snap probe or a single pose: cheap to build, and
# waiting a frame per request would only slow the search down.
_FRAME_GATED_BATCH = 200

# Operating modes NOVA reports limits for, widest-first is NOT assumed - we take
# the union (widest [lower, upper] per joint) across all of them.
# Everything the NOVA client raises for a failed call. Catching plain Exception
# would swallow bugs in the request we build, and those look exactly like a
# region of space the robot cannot reach.
_API_ERRORS = (OpenApiException, aiohttp.ClientError, asyncio.TimeoutError)


# Joint-limit margin drawn full green: every joint at least half its range
# away from a limit. The tightest joint decides a margin, so a solution rarely
# gets further than that, and a scale up to 1 would leave the green end unused.
FULL_GREEN_MARGIN = 0.5


def grade_margins(margins: np.ndarray) -> np.ndarray:
    """Colour position in [0, 1] for joint-limit margins, on one fixed scale.

    Fixed rather than fitted to each cloud, so the same colour means the same
    room at every orientation and in every cloud.
    """
    margins = np.asarray(margins, dtype=np.float64)
    return np.clip(margins / FULL_GREEN_MARGIN, 0.0, 1.0).astype(np.float32)


class IkRequestFailed(RuntimeError):
    """A NOVA IK request did not answer.

    Raised rather than folded into "nothing solved": a pose that is out of
    reach and a NOVA that cannot be asked look identical from the return value,
    and telling the user their pose is unreachable when the request never
    arrived sends them looking in the wrong place.
    """


def auto_joint_limits(operation_limits) -> list | None:
    """Per-joint position limits for automatic mode, or None.

    The same set ``fetch_joint_configs_for_pose`` solves the placed pose
    against. Widening this - merging the manual/T1/T2 ranges, say - draws a
    cloud and snaps to points the verdict then rejects, because the verdict is
    clamped to automatic mode either way. A cage configured in NOVA belongs in
    the picture; two different answers to "can it reach" do not.
    """
    if operation_limits is None:
        return None
    limit_set = getattr(operation_limits, "auto_limits", None)
    if limit_set is None or not limit_set.joints:
        return None
    return [joint.position for joint in limit_set.joints]


# Snap search resolution. The innermost shell decides how small an offset the
# search can even express, so it sits at a millimetre; the refinement bisects
# until the remaining span is below _SNAP_REFINE_EPS_MM or the steps run out.
_SNAP_MIN_RADIUS_MM = 1.0
_SNAP_REFINE_STEPS = 6
_SNAP_REFINE_EPS_MM = 0.25

_SPHERE_DIRECTION_CACHE: dict[int, np.ndarray] = {}


def _sphere_directions(n: int) -> np.ndarray:
    """(n, 3) unit vectors spread evenly over the sphere (Fibonacci lattice)."""
    cached = _SPHERE_DIRECTION_CACHE.get(n)
    if cached is not None:
        return cached
    i = np.arange(n, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = np.pi * (1.0 + 5.0**0.5) * i
    dirs = np.stack(
        [np.sin(phi) * np.cos(theta), np.sin(phi) * np.sin(theta), np.cos(phi)],
        axis=1,
    )
    _SPHERE_DIRECTION_CACHE[n] = dirs
    return dirs


def _log_dh_chain(model_name: str, chain: dh_chain.DHChain) -> None:
    """Log the DH parameters + joint limits actually used for this sweep.

    Diagnostic only - if the workspace cloud looks geometrically wrong (e.g.
    a half sphere where a full one is expected), compare these numbers
    against the robot's known-correct DH spec: a wrong ``alpha``/``d`` on a
    wrist joint, or a joint limit range that's narrower than the true
    mechanical range, produces exactly that kind of malformed shape. This
    reads/logs only what the backend already reported - it can't tell us
    whether that data is itself correct.
    """
    carb.log_info(f"[Envelope] DH chain for {model_name} ({chain.num_joints} joints):")
    for i in range(chain.num_joints):
        carb.log_info(
            f"[Envelope]  joint {i}: a={chain.a[i]:.2f}mm "
            f"alpha={np.degrees(chain.alpha[i]):.2f}deg d={chain.d[i]:.2f}mm "
            f"theta0={np.degrees(chain.theta0[i]):.2f}deg sign={chain.sign[i]:+.0f} "
            f"limits=[{np.degrees(chain.lower[i]):.1f}, "
            f"{np.degrees(chain.upper[i]):.1f}]deg "
            f"(span={np.degrees(chain.upper[i] - chain.lower[i]):.1f}deg)"
        )


@dataclass
class EnvelopeContext:
    """Everything needed to compute the envelope for one motion group."""

    model_name: str
    cell: str
    inverse_kinematics: InverseKinematicsProbe
    chain: dh_chain.DHChain
    joint_position_limits: list | None
    nova_tcp_offset: wb_v2_models.Pose | None
    tcp_offset_mm: np.ndarray | None  # 4x4, flange->TCP, mm
    # World->base offset from the API description, forwarded to NOVA IK so the
    # motion group's actual mounting is always respected.
    mounting: wb_v2_models.Pose | None = None
    available_tcps: list = field(default_factory=list)  # TCP names from description
    active_tcp: str | None = None
    _api_client: object = field(default=None, repr=False)
    # Raw sweep positions (N, 3) mm. Sampling and FK dominate the cost and
    # neither depends on the voxel size, so a density change re-voxelizes this
    # instead of re-sweeping.
    _sweep_positions: np.ndarray | None = field(default=None, repr=False)
    _sweep_samples: int = field(default=0, repr=False)


@dataclass
class Envelope:
    centres_mm: np.ndarray  # (M, 3) voxel centres the TCP can pass through, mm
    # (M,) in [0, 1]. For the sweep: how densely joint space maps there. For an
    # oriented envelope: how far the IK solution stays from the joint limits.
    likelihood: np.ndarray
    voxel_mm: float
    # Orientation the voxels were tested at, or None for the orientation-free
    # sweep. A caller can tell the two apart without inspecting the values.
    orientation_rotvec: list[float] | None = None
    # True when at least one IK batch went unanswered, so voxels are missing
    # that nobody decided against. Drawn, but not to be read as "all there is".
    incomplete: bool = False


class EnvelopeService:
    # Set by the last failed IK request, so a search that found nothing can say
    # whether it looked or could not ask.
    _last_ik_error: str | None = None

    async def prepare(
        self,
        motion_group_prim: Usd.Prim,
        tcp_name=None,
        mounting_override: wb_v2_models.Pose | None = None,
    ) -> EnvelopeContext | None:
        """Fetch the description + build the DH chain / TCP / IK client.

        ``tcp_name`` selects a TCP from the description; without it (or when
        the name is unknown) the flange is used. ``mounting_override`` replaces
        the description's own ``mounting`` in IK requests (the tool window
        pre-fills it from the API and lets the user edit it); None keeps the
        API value.
        """
        from wandelbots.omni.manipulators.motion_group import (
            get_motion_group_configuration_from_prim,
        )

        config = get_motion_group_configuration_from_prim(motion_group_prim)
        if config is None:
            carb.log_warn(
                f"Envelope: {motion_group_prim.GetPath()} is not a motion group"
            )
            return None
        stream = config.motion_stream_configuration
        api_client = stream.get_api_client()
        try:
            desc = await asyncio.wait_for(
                wb_v2.MotionGroupApi(api_client).get_motion_group_description(
                    cell=stream.cell,
                    controller=stream.controller,
                    motion_group=stream.motion_group,
                ),
                timeout=5.0,
            )
        except Exception as exc:
            carb.log_warn(f"Envelope: could not fetch description: {exc}")
            await self._close_client(api_client)
            return None

        if not desc.dh_parameters:
            carb.log_warn("Envelope: description has no dh_parameters")
            await self._close_client(api_client)
            return None

        try:
            joint_position_limits = auto_joint_limits(desc.operation_limits)
        except Exception as exc:
            carb.log_warn(f"Envelope: could not read joint limits: {exc}")
            joint_position_limits = None

        chain = dh_chain.dh_chain_from_description(
            desc.dh_parameters,
            joint_position_limits,
            kinematic_chain_offset=getattr(desc, "kinematic_chain_offset", None),
        )
        _log_dh_chain(desc.motion_group_model, chain)

        available_tcps = list(desc.tcps.keys()) if desc.tcps else []
        nova_tcp_offset = None
        tcp_offset_mm = None
        active_tcp = None
        if tcp_name and desc.tcps and tcp_name in desc.tcps:
            nova_tcp_offset = desc.tcps[tcp_name].pose
            active_tcp = tcp_name
            carb.log_info(f"Envelope: using description TCP '{tcp_name}'")
        # NOVA's chain is [DH end] -> flange_offset -> [flange] -> tcp_offset,
        # so the local FK sweep must prepend flange_offset to the TCP matrix
        # or a rotated flange with a translated TCP misplaces every sample.
        flange_offset = getattr(desc, "flange_offset", None)
        flange_mm = (
            pose_to_matrix([*flange_offset.position, *flange_offset.orientation])
            if flange_offset is not None
            else None
        )
        if nova_tcp_offset is not None:
            tcp_offset_mm = pose_to_matrix(
                [*nova_tcp_offset.position, *nova_tcp_offset.orientation]
            )
            if flange_mm is not None:
                tcp_offset_mm = flange_mm @ tcp_offset_mm
            tcp_offset_mm = tcp_offset_mm.astype(np.float32)
        elif flange_mm is not None:
            tcp_offset_mm = flange_mm.astype(np.float32)

        carb.log_info(
            f"Envelope ready for {desc.motion_group_model}: {chain.num_joints} joints"
        )
        return EnvelopeContext(
            model_name=desc.motion_group_model,
            cell=stream.cell,
            inverse_kinematics=InverseKinematicsProbe(api_client),
            chain=chain,
            joint_position_limits=joint_position_limits,
            nova_tcp_offset=nova_tcp_offset,
            tcp_offset_mm=tcp_offset_mm,
            mounting=(
                mounting_override if mounting_override is not None else desc.mounting
            ),
            available_tcps=available_tcps,
            active_tcp=active_tcp,
            _api_client=api_client,
        )

    def compute_envelope(
        self,
        ctx: EnvelopeContext,
        voxel_mm: float,
        num_samples: int = _WORKSPACE_SAMPLES,
        seed: int = 0,
    ) -> Envelope:
        """Voxels the TCP can pass through, from a local forward-kinematics sweep.

        Joint space is sampled within the limits, run through the DH chain on the
        GPU and reduced to occupied voxels. Per-voxel ``likelihood`` is how many
        samples landed there, normalised - a cheap stand-in for how much freedom
        the arm has around that point.

        Deliberately NOT inverse kinematics. Asking NOVA per voxel was exact but
        cost ~150k requests for one recompute, which is absurd for what the cloud
        is: an orientation-agnostic hint about where to put a pose. The binding
        answer for a placed pose is one IK call, in the tool window.

        Synchronous: the sweep is a kernel launch and a numpy reduce, so there is
        nothing to await and no progress worth reporting.
        """
        if ctx._sweep_positions is None or ctx._sweep_samples != num_samples:
            joints = warp_fk.sample_joint_space(ctx.chain, num_samples, seed=seed)
            ctx._sweep_positions = warp_fk.fk_sweep_positions(
                ctx.chain, joints, ctx.tcp_offset_mm
            )
            ctx._sweep_samples = num_samples
            carb.log_info(
                f"Envelope sweep: {num_samples} samples via {warp_fk.gpu_backend()}"
            )

        centres, counts = warp_fk.voxelize(ctx._sweep_positions, voxel_mm)
        if counts.size == 0:
            return Envelope(centres, np.empty((0,), np.float32), voxel_mm)
        # Normalise against a high percentile rather than the maximum: a single
        # dense voxel near the base would otherwise push the whole cloud dark.
        reference = float(np.percentile(counts, 95)) or 1.0
        likelihood = np.clip(counts / reference, 0.0, 1.0).astype(np.float32)
        carb.log_info(
            f"Envelope: {centres.shape[0]} voxels at {voxel_mm:.0f}mm "
            f"from {num_samples} samples"
        )
        return Envelope(centres_mm=centres, likelihood=likelihood, voxel_mm=voxel_mm)

    async def compute_oriented_envelope(
        self,
        ctx: EnvelopeContext,
        orientation_rotvec: list[float],
        voxel_mm: float,
        num_samples: int = _WORKSPACE_SAMPLES,
        seed: int = 0,
    ) -> Envelope:
        """Voxels the TCP can reach AT ``orientation_rotvec``, decided by NOVA IK.

        Which positions are reachable depends strongly on the orientation asked
        for, so the orientation-free sweep answers a different question than the
        one a user placing an oriented pose is asking. This narrows the sweep to
        the orientation at hand: the sweep supplies the candidates - a position
        reachable at one orientation is reachable at some orientation, so it is
        in there - and NOVA decides each of them.

        The cost is in batches, not in voxels: the candidates go out
        ``_MAX_IK_BATCH`` at a time, so a 1.3 m arm at 60 mm is tens of requests
        rather than the ~150k single ones that made per-voxel IK absurd.

        Voxels from an unanswered batch are left out - nothing decided they are
        reachable - but the result is marked ``incomplete``, so a caller can say
        the envelope is partial rather than present a smaller one as the truth.
        """
        sweep = self.compute_envelope(ctx, voxel_mm, num_samples=num_samples, seed=seed)
        if sweep.centres_mm.size == 0:
            return Envelope(
                sweep.centres_mm,
                sweep.likelihood,
                voxel_mm,
                orientation_rotvec=list(orientation_rotvec),
            )

        self._last_ik_error = None
        centres = sweep.centres_mm
        chunks = [
            (start, centres[start : start + _MAX_IK_BATCH])
            for start in range(0, centres.shape[0], _MAX_IK_BATCH)
        ]
        seed_vec = self._make_seeds(ctx, 1)[0]
        results = await asyncio.gather(
            *(
                self._ik_batch(ctx, points, orientation_rotvec, seed_vec)
                for _start, points in chunks
            )
        )

        margins = np.full(centres.shape[0], np.nan)
        failed = 0
        for (start, points), batch_margins in zip(chunks, results):
            if batch_margins is None:
                failed += 1
                continue
            margins[start : start + points.shape[0]] = batch_margins
        keep = np.isfinite(margins)

        carb.log_info(
            f"Envelope at orientation {orientation_rotvec}: "
            f"{int(keep.sum())} of {centres.shape[0]} voxels reachable "
            f"({len(chunks)} batches, {failed} unanswered)"
        )
        return Envelope(
            centres_mm=centres[keep],
            likelihood=grade_margins(margins[keep]),
            voxel_mm=voxel_mm,
            orientation_rotvec=list(orientation_rotvec),
            incomplete=failed > 0,
        )

    async def find_nearest_reachable(
        self,
        ctx: EnvelopeContext,
        position_mm,
        orientation_rotvec: list[float],
        max_radius_mm: float = 500.0,
        min_radius_mm: float = _SNAP_MIN_RADIUS_MM,
        shells: int = 14,
        seeds: int = 2,
        refine_steps: int = _SNAP_REFINE_STEPS,
    ) -> np.ndarray | None:
        """Nearest base-frame position (mm) reachable at a fixed orientation.

        Continuous local search around the pose, NOT limited to the envelope's
        precomputed voxel grid: batched IK over spherical shells of sample
        points at growing radii, nearest shell first. Every point in a shell is
        equidistant from the pose, so the first shell with any IK solution
        yields the (near-)nearest reachable point.

        The shell radii grow GEOMETRICALLY from ``min_radius_mm``, so a pose
        that misses the reachable space by a millimetre is answered with a
        millimetre-scale offset. Evenly spaced shells could not do that: their
        first shell was max_radius/shells - 50 mm at the defaults - so every
        near miss was reported as a 50 mm snap, and the pose was dragged that
        far for want of resolution.

        The hit is then refined by bisecting the radius along the direction it
        was found on, which brings the far shells (where the geometric spacing
        is coarse) back to the same order of accuracy.

        Returns the pose position itself when it is already reachable, None
        when nothing within ``max_radius_mm`` solves.
        """
        p0 = np.asarray(position_mm, dtype=np.float64)
        self._last_ik_error = None
        directions = _sphere_directions(32)
        lo_mm = max(float(min_radius_mm), 1e-3)
        radii = np.geomspace(lo_mm, float(max_radius_mm), max(int(shells), 2))
        seed_vectors = self._make_seeds(ctx, seeds)

        shells_pts = [p0[None, :]]  # shell 0: the pose itself (already reachable?)
        shells_pts += [p0[None, :] + directions * r for r in radii]

        for shell_idx, pts in enumerate(shells_pts):
            for seed_vec in seed_vectors:
                solved = await self._solved_batch(
                    ctx, pts, orientation_rotvec, seed_vec
                )
                if solved is None:
                    continue
                for i, sol in enumerate(solved):
                    if not sol:
                        continue
                    if shell_idx == 0:
                        carb.log_info("Envelope snap: the pose itself is reachable")
                        return pts[i]
                    hit_radius = float(radii[shell_idx - 1])
                    inner = float(radii[shell_idx - 2]) if shell_idx >= 2 else 0.0
                    point, radius = await self._refine_snap_radius(
                        ctx,
                        p0,
                        directions[i],
                        orientation_rotvec,
                        seed_vec,
                        inner,
                        hit_radius,
                        refine_steps,
                    )
                    carb.log_info(
                        f"Envelope snap: reachable point at {radius:.2f}mm "
                        f"(shell {shell_idx} hit at {hit_radius:.2f}mm, refined "
                        f"from the {inner:.2f}mm shell)"
                    )
                    return point
        if self._last_ik_error is not None:
            # Every shell came back empty, but at least one request never
            # answered - so "nothing is reachable" would be a guess.
            raise IkRequestFailed(
                f"NOVA did not answer the reachability search: {self._last_ik_error}"
            )
        return None

    async def first_reachable_point(
        self,
        ctx: EnvelopeContext,
        points_mm,
        orientation_rotvec: list[float],
        seeds: int = 2,
    ) -> np.ndarray | None:
        """First of *points_mm*, in the order given, that IK solves at this
        orientation - or None when none of them does.

        For checking a handful of candidates that came from somewhere else (a
        precomputed envelope, say) against the orientation actually asked for.
        The order is the caller's priority, so the answer is the best candidate
        that survives IK rather than merely the nearest one.
        """
        points = np.asarray(points_mm, dtype=np.float64)
        if points.size == 0:
            return None
        best: int | None = None
        for seed_vec in self._make_seeds(ctx, seeds):
            solved = await self._solved_batch(ctx, points, orientation_rotvec, seed_vec)
            if solved is None:
                continue
            for index, solution in enumerate(solved):
                if not solution:
                    continue
                # Seeds are tried in turn, so a later seed can solve a
                # higher-priority candidate the first one missed.
                if best is None or index < best:
                    best = index
                break
            if best == 0:
                break
        return None if best is None else points[best]

    @staticmethod
    def _in_nova_frame(
        ctx: EnvelopeContext, points, orientation_rotvec: list[float]
    ) -> tuple[np.ndarray, list[float]]:
        """Base-frame *points* at one orientation, moved into NOVA's frame.

        The points share the orientation, so the mounting is applied to it once
        and to all positions in one numpy step (see base_pose_to_world).
        """
        positions = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        orientation = [float(v) for v in orientation_rotvec]
        if ctx.mounting is None:
            return positions, orientation
        mount = pose_to_matrix(
            list(ctx.mounting.position) + list(ctx.mounting.orientation)
        )
        positions = positions @ mount[:3, :3].T + mount[:3, 3]
        orientation = base_pose_to_world([0.0, 0.0, 0.0, *orientation], ctx.mounting)
        return positions, orientation[3:]

    async def _refine_snap_radius(
        self,
        ctx: EnvelopeContext,
        p0: np.ndarray,
        direction: np.ndarray,
        orientation_rotvec: list[float],
        seed_vec,
        inner_mm: float,
        outer_mm: float,
        steps: int,
    ) -> tuple[np.ndarray, float]:
        """Shrink the snap distance along *direction* by bisecting the radius.

        ``inner_mm`` is the largest radius known NOT to solve on this shell
        sequence, ``outer_mm`` the one that just did. Each step halves the
        remaining span; a step that solves becomes the new best.
        """
        best = p0 + direction * outer_mm
        best_mm = outer_mm
        lo, hi = inner_mm, outer_mm
        for _ in range(max(int(steps), 0)):
            mid = 0.5 * (lo + hi)
            if hi - lo < _SNAP_REFINE_EPS_MM:
                break
            candidate = p0 + direction * mid
            solved = await self._solved_batch(
                ctx, candidate[None, :], orientation_rotvec, seed_vec
            )
            if solved is not None and solved[0]:
                best, best_mm, hi = candidate, mid, mid
            else:
                lo = mid
        return best, best_mm

    async def _ik_batch(self, ctx, points, orientation_rotvec, seed_vec):
        async with _ik_request_slot:
            if len(points) >= _FRAME_GATED_BATCH:
                async with _ik_frame_gate:
                    await omni.kit.app.get_app().next_update_async()
            positions, orientation = self._in_nova_frame(
                ctx, points, orientation_rotvec
            )
            body = encode_ik_request(
                self._request_fields(ctx, seed_vec), positions, orientation
            )
            return await self._request_ik(ctx, body)

    async def _solved_batch(self, ctx, points, orientation_rotvec, seed_vec):
        margins = await self._ik_batch(ctx, points, orientation_rotvec, seed_vec)
        return None if margins is None else np.isfinite(margins)

    @staticmethod
    def _request_fields(ctx: EnvelopeContext, seed_vec) -> dict:
        return wb_v2_models.InverseKinematicsRequest(
            motion_group_model=ctx.model_name,
            tcp_poses=[],
            tcp_offset=ctx.nova_tcp_offset,
            mounting=ctx.mounting,
            joint_position_limits=ctx.joint_position_limits,
            reference_joint_position=seed_vec,
        ).to_dict()

    async def _request_ik(self, ctx, body: str) -> np.ndarray | None:
        try:
            return await asyncio.wait_for(
                ctx.inverse_kinematics.joint_margins(
                    ctx.cell, body, ctx.chain.lower, ctx.chain.upper
                ),
                timeout=10.0,
            )
        except _API_ERRORS as exc:
            carb.log_warn(f"Envelope IK batch failed: {exc}")
            self._last_ik_error = str(exc)
            return None

    def _make_seeds(self, ctx: EnvelopeContext, seeds: int) -> list[list[float]]:
        chain = ctx.chain
        mid = ((chain.lower + chain.upper) / 2.0).astype(float)
        out = [mid.tolist()]
        for base in _BASE_SEED_ANGLES:
            if len(out) >= max(1, seeds):
                break
            s = mid.copy()
            if s.shape[0] > 0:
                s[0] = base
            out.append(s.tolist())
        return out[: max(1, seeds)]

    @staticmethod
    async def _close_client(api_client) -> None:
        try:
            close = getattr(api_client, "close", None)
            if close is not None:
                await close()
        except Exception:
            pass

    def close(self, ctx: EnvelopeContext | None) -> None:
        if ctx and ctx.inverse_kinematics is not None:
            import omni.kit.async_engine as ae

            ae.run_coroutine(ctx.inverse_kinematics.close())
        if ctx and ctx._api_client is not None:
            try:
                run = getattr(ctx._api_client, "close", None)
                if run:
                    import omni.kit.async_engine as ae

                    ae.run_coroutine(ctx._api_client.close())
            except Exception:
                pass
            ctx._api_client = None


_envelope_service: EnvelopeService | None = None


def get_envelope_service() -> EnvelopeService:
    global _envelope_service
    if _envelope_service is None:
        _envelope_service = EnvelopeService()
    return _envelope_service
