"""Ghost mesh cache: computed meshes, in-flight background builds, and the
pending-mesh marker used to repair duplicates made mid-build.

Deliberately schema-free (no wb_schema/GhostObjectAPI): callers resolve the
source tool and TCP prim themselves and pass plain USD types in. Keeps this
module usable/testable without the ghost-object schema layer in teaching.py.
"""

import asyncio
import hashlib
from typing import NamedTuple

import carb
import numpy as np
import omni.usd
from pxr import Gf, Usd, UsdGeom

from wandelbots.omni.utils.mesh import MeshUtils

# customData flag: True while a ghost mesh is still being built in the
# background. Set on the prim itself (not just in-memory) so it survives a
# USD-level duplicate - Kit's Duplicate clones customData verbatim, which is
# exactly what lets GhostMeshCache.repair recognize a duplicate made
# mid-build and finish the job for it too.
GHOST_MESH_PENDING_KEY = "wandelbots:ghostMeshPending"


class _PendingBuild(NamedTuple):
    task: asyncio.Task
    source_path: str
    source_stamp: str
    offset: np.ndarray


class GhostMeshCache:
    """Computed ghost meshes keyed by (source prim path, source-to-TCP
    offset, source geometry stamp). The offset and stamp fully determine
    the baked, TCP-centered mesh for a tool, so converting the same tool
    with an approximately equal offset and unchanged geometry reuses the
    mesh instead of rebuilding. Entries: (source_path, stamp, offset 4x4,
    mesh data)."""

    _mesh_cache: list = []
    # Approximate offset match: rotation entries and translation in stage units
    ROTATION_TOLERANCE = 1e-4
    TRANSLATION_TOLERANCE = 1e-2
    # target_path -> in-flight background build. Lets repair() tell a ghost
    # that's already building apart from one that was left pending (a
    # duplicate, or a stage saved mid-build) and needs restarting, and lets
    # a repaired duplicate piggyback on a build for the same tool+TCP
    # instead of starting a redundant second one (see find_pending_build).
    _pending_builds: dict[str, _PendingBuild] = {}

    @staticmethod
    def compute_transforms(
        source_prim: Usd.Prim, tcp_prim_path: str
    ) -> tuple[Gf.Matrix4d, Gf.Matrix4d]:
        """Return (source_to_tcp_offset, mesh_offset_transform) for
        source_prim placed at tcp_prim_path.

        Both are pose-independent - the offset between the tool and its TCP,
        not the TCP's current world position - so source_to_tcp_offset
        doubles as the mesh cache key, and mesh_offset_transform is the
        frame the ghost mesh is baked into (TCP-centered).
        """
        stage: Usd.Stage = source_prim.GetStage()
        tcp_transform: Gf.Matrix4d = omni.usd.get_world_transform_matrix(
            stage.GetPrimAtPath(tcp_prim_path)
        ).GetOrthonormalized()
        source_transform = omni.usd.get_world_transform_matrix(
            source_prim
        ).GetOrthonormalized()
        tcp_transform_inverse = tcp_transform.GetInverse()
        source_to_tcp_offset = source_transform * tcp_transform_inverse
        return source_to_tcp_offset, tcp_transform_inverse

    @staticmethod
    def compute_source_stamp(source_prim: Usd.Prim) -> str:
        """Content stamp of the tool's raw mesh data.

        Compared at cache lookup time so in-stage edits of a cached tool
        (geometry, reference/variant swaps) miss the cache and rebuild. Raw
        attribute bytes are bit-stable across reads, so equal stamps mean
        unchanged geometry. Costs milliseconds even for large tools, only
        when a ghost is created - deliberately no change listener, which
        would run on every stage notice and cost frame time.
        """
        digest = hashlib.sha1(usedforsecurity=False)
        for prim, points, indices, _counts in MeshUtils.iter_source_meshes(source_prim):
            digest.update(prim.GetPath().pathString.encode())
            digest.update(np.asarray(points, dtype=np.float32).tobytes())
            digest.update(np.asarray(indices, dtype=np.int32).tobytes())
        return digest.hexdigest()

    @staticmethod
    def _offsets_match(offset_a: np.ndarray, offset_b: np.ndarray) -> bool:
        rotation_close = np.allclose(
            offset_a[:3, :3],
            offset_b[:3, :3],
            atol=GhostMeshCache.ROTATION_TOLERANCE,
        )
        translation_close = np.allclose(
            offset_a[3, :3],
            offset_b[3, :3],
            atol=GhostMeshCache.TRANSLATION_TOLERANCE,
        )
        return rotation_close and translation_close

    @staticmethod
    def find_mesh(source_path: str, tcp_offset: Gf.Matrix4d, source_stamp: str):
        """Approximate cache lookup: same source prim with unchanged geometry
        and a source-to-TCP offset within the rotation/translation
        tolerances."""
        offset_matrix = np.asarray(tcp_offset)
        for (
            cached_path,
            cached_stamp,
            cached_offset,
            mesh_data,
        ) in GhostMeshCache._mesh_cache:
            if cached_path != source_path or cached_stamp != source_stamp:
                continue
            if GhostMeshCache._offsets_match(cached_offset, offset_matrix):
                return mesh_data
        return None

    @staticmethod
    def find_pending_build(
        source_path: str, tcp_offset: Gf.Matrix4d, source_stamp: str
    ) -> asyncio.Task | None:
        """Find an in-flight build for the same tool+TCP+geometry, if any -
        e.g. the ghost a pending duplicate was made from. Mirrors
        find_mesh's approximate offset match."""
        offset_matrix = np.asarray(tcp_offset)
        for pending in GhostMeshCache._pending_builds.values():
            if (
                pending.source_path != source_path
                or pending.source_stamp != source_stamp
            ):
                continue
            if GhostMeshCache._offsets_match(pending.offset, offset_matrix):
                return pending.task
        return None

    @staticmethod
    def store_mesh(
        source_path: str,
        source_stamp: str,
        tcp_offset: Gf.Matrix4d,
        mesh_prim: UsdGeom.Mesh,
    ) -> None:
        mesh_data = (
            mesh_prim.GetPointsAttr().Get(),
            mesh_prim.GetFaceVertexIndicesAttr().Get(),
            mesh_prim.GetFaceVertexCountsAttr().Get(),
        )
        GhostMeshCache._mesh_cache.append(
            (source_path, source_stamp, np.asarray(tcp_offset), mesh_data)
        )

    @staticmethod
    def is_pending(prim: Usd.Prim) -> bool:
        return bool(prim.GetCustomDataByKey(GHOST_MESH_PENDING_KEY))

    @staticmethod
    def is_building(target_path: str) -> bool:
        return target_path in GhostMeshCache._pending_builds

    @staticmethod
    def start_build(
        source_prim: Usd.Prim,
        target_path: str,
        mesh_offset_transform: Gf.Matrix4d,
        source_path: str,
        source_stamp: str,
        relative_tcp_transform: Gf.Matrix4d,
    ) -> asyncio.Task:
        """Mark target_path mesh-pending and start the progressive build.

        Tracked in _pending_builds so repair() never double-builds a ghost
        that's already building, and a repaired duplicate can piggyback on
        this build instead of starting its own (see find_pending_build). The
        done callback clears the pending marker, stores the refined mesh in
        the cache, and drops the registry entry.
        """
        stage: Usd.Stage = source_prim.GetStage()
        stage.GetPrimAtPath(target_path).SetCustomDataByKey(
            GHOST_MESH_PENDING_KEY, True
        )
        carb.log_info(
            f"Optimizing ghost object mesh for {target_path} in the background."
        )
        task = MeshUtils.fill_ghost_mesh_progressively(
            source_prim=source_prim,
            target_path=target_path,
            mesh_offset_transform=mesh_offset_transform,
        )
        GhostMeshCache._pending_builds[target_path] = _PendingBuild(
            task, source_path, source_stamp, np.asarray(relative_tcp_transform)
        )

        def _on_build_done(task: asyncio.Task) -> None:
            GhostMeshCache._pending_builds.pop(target_path, None)
            prim = stage.GetPrimAtPath(target_path)
            if prim:
                prim.ClearCustomDataByKey(GHOST_MESH_PENDING_KEY)
            if task.cancelled():
                return
            error = task.exception()
            if error is not None:
                carb.log_warn(f"Ghost mesh build for {target_path} failed: {error}")
                return
            refined_mesh = task.result()
            if refined_mesh is not None:
                GhostMeshCache.store_mesh(
                    source_path, source_stamp, relative_tcp_transform, refined_mesh
                )

        task.add_done_callback(_on_build_done)
        return task

    @staticmethod
    def clear() -> None:
        """Drop all cached ghost meshes and in-flight build tracking.

        Must be called on stage changes: both key on prim paths, and a new
        stage may carry a different tool at the same path. In-stage tool
        edits are detected via the geometry stamp at lookup time. Tracked
        build tasks are not cancelled - they already guard against a closed
        stage before writing (see MeshUtils.fill_ghost_mesh_progressively)
        and just no-op.
        """
        GhostMeshCache._mesh_cache.clear()
        GhostMeshCache._pending_builds.clear()
