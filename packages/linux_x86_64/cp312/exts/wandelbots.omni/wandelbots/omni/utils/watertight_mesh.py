"""Watertight mesh union via convex parts (manifold3d + CoACD).

This module is free of Kit/omni imports so it can run as a standalone
worker process (see main below). The native deps are nanobind modules that
hard-abort the process when initialized twice, which happens on extension
hot reload if they are imported inside Kit - so MeshUtils runs this
pipeline in a subprocess and the Kit process never loads them.
The native imports are kept inside the functions so importing this module
stays side-effect free.
"""

import os
import sys

# --- Mesh quality tuning -----------------------------------------------------
# The refined mesh builds in the background behind an instant hull preview
# (see MeshUtils.fill_ghost_mesh_progressively), so it no longer blocks the
# user - these can favor visual fidelity over raw build speed. Lower
# DECOMPOSE_THRESHOLD/CONCAVITY_TOLERANCE and higher COACD_* resolution/
# iteration values keep more detail on complex concave shapes, at the cost of
# a slower (still backgrounded) refine. Edit these directly to retune.
DECOMPOSE_THRESHOLD = 0.04
CONCAVITY_TOLERANCE = 1.1
SIMPLIFY_RATIO = 0.003
COACD_PREPROCESS_RESOLUTION = 50
COACD_RESOLUTION = 2000
COACD_MCTS_NODES = 20
COACD_MCTS_ITERATIONS = 150
COACD_MCTS_MAX_DEPTH = 3
COACD_MAX_CH_VERTEX = 256


def _prepare_worker_environment() -> None:
    """Make the bundled deps importable, however this process was launched.

    Runs only in the worker process (Kit always has carb loaded). Debugger
    subprocess injection (pydevd auto-attach) rewrites both argv and the
    environment of the worker, so no single channel is reliable: try the
    explicit argv path, a dedicated env var pydevd does not touch, and the
    pip_prebundle shipped next to this extension. Also drop this package
    directory from sys.path - it contains stdlib-shadowing modules
    (math.py) that would break the numpy import.
    """
    if "carb" in sys.modules:
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sys.path = [
        path_entry
        for path_entry in sys.path
        if os.path.abspath(path_entry or ".") != script_dir
    ]

    extension_root = os.path.abspath(os.path.join(script_dir, "..", "..", ".."))
    candidates = [
        sys.argv[1] if len(sys.argv) > 1 else "",
        os.environ.get("WANDELBOTS_MESH_WORKER_DEPS", ""),
        os.path.join(extension_root, "pip_prebundle"),
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.insert(0, candidate)


_prepare_worker_environment()

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

try:
    import numpy as np  # noqa: E402
except ImportError as error:
    # numpy wraps native-module failures in a generic message; surface the
    # real cause and the interpreter details for the parent's error log.
    raise ImportError(
        f"mesh worker numpy import failed: cause=[{error.__cause__ or error}] "
        f"executable={sys.executable} version={sys.version_info[:3]} "
        f"sys.path={sys.path}"
    ) from error
from numpy.typing import NDArray  # noqa: E402

# One submesh as numpy arrays: (vertices Nx3 float, triangle indices Mx3 int)
SubMesh = tuple[NDArray[np.floating], NDArray[np.integer]]


def _split_connected_components(vertices: NDArray, triangles: NDArray) -> list[SubMesh]:
    """Split a triangle soup into components connected via shared vertices.

    CAD meshes routinely pack many disconnected shells into one Mesh prim;
    a shell is usually convex-ish even when the combined prim is concave,
    so splitting lets most geometry take the cheap hull path instead of a
    CoACD decomposition.
    """
    parent = np.arange(len(vertices), dtype=np.int64)

    def find(index: int) -> int:
        root = index
        while parent[root] != root:
            root = parent[root]
        while parent[index] != root:  # path compression
            parent[index], index = root, parent[index]
        return root

    for vertex_a, vertex_b, vertex_c in triangles:
        root_a = find(vertex_a)
        parent[find(vertex_b)] = root_a
        parent[find(vertex_c)] = root_a

    component_roots = np.array([find(index) for index in triangles[:, 0]])
    components: list[SubMesh] = []
    for root in np.unique(component_roots):
        component_triangles = triangles[component_roots == root]
        used_vertices = np.unique(component_triangles)
        index_remap = np.zeros(len(vertices), dtype=np.int32)
        index_remap[used_vertices] = np.arange(len(used_vertices), dtype=np.int32)
        components.append((vertices[used_vertices], index_remap[component_triangles]))
    return components


def _cluster_decimate(
    vertices: NDArray, triangles: NDArray, cell_size: float
) -> SubMesh:
    """Cheap vertex-clustering decimation: weld vertices to a grid of
    cell_size and drop collapsed triangles. Used to shrink dense shells
    before the (input-size-bound) CoACD decomposition."""
    grid_cells = np.floor(vertices / cell_size).astype(np.int64)
    _, first_index, inverse = np.unique(
        grid_cells, axis=0, return_index=True, return_inverse=True
    )
    welded_triangles = inverse[triangles]
    non_degenerate = (
        (welded_triangles[:, 0] != welded_triangles[:, 1])
        & (welded_triangles[:, 1] != welded_triangles[:, 2])
        & (welded_triangles[:, 0] != welded_triangles[:, 2])
    )
    return vertices[first_index], welded_triangles[non_degenerate].astype(np.int32)


def _mesh_volume(vertices: NDArray, triangles: NDArray) -> float:
    """Signed-tetrahedron volume sum; exact for closed meshes, an estimate
    for open triangle soup (good enough as a concavity heuristic)."""
    corners = np.asarray(vertices, dtype=np.float64)[np.asarray(triangles)]
    signed_volumes = np.einsum(
        "ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])
    )
    return abs(signed_volumes.sum()) / 6.0


def _convex_hull(points: NDArray):
    """Convex hull manifold of the points, or None if degenerate."""
    import manifold3d

    if len(points) < 4:
        return None
    hull = manifold3d.Manifold.hull_points(np.asarray(points, dtype=np.float32))
    return None if hull.is_empty() else hull


def _manifold_volume(manifold) -> float:
    """Volume of a manifold3d object."""
    mesh = manifold.to_mesh()
    return _mesh_volume(mesh.vert_properties, mesh.tri_verts)


def _classify_part(
    vertices: NDArray,
    triangles: NDArray,
    concavity_tolerance: float,
    tiny_hull_volume: float,
) -> tuple[object | None, SubMesh | None]:
    """Classify one part as convex-ish or concave.

    Returns (hull, None) for a part its convex hull represents well,
    (None, part) for a concave part that needs decomposition, and
    (None, None) for degenerate geometry. Concave parts whose hull volume
    is below tiny_hull_volume count as convex-ish: they are visually
    irrelevant and not worth a decomposition.
    """
    hull = _convex_hull(vertices)
    if hull is None:
        return None, None
    hull_volume = _manifold_volume(hull)
    part_volume = _mesh_volume(vertices, triangles)
    is_concave = part_volume > 0 and hull_volume > concavity_tolerance * part_volume
    if is_concave and hull_volume > tiny_hull_volume:
        return None, (vertices, triangles)
    return hull, None


def _collect_convex_hulls(
    submeshes: list[SubMesh],
    concavity_tolerance: float,
    tiny_hull_volume: float,
    cell_size: float,
) -> tuple[list, list[SubMesh]]:
    """Hull every convex-ish part; return (hulls, concave shells for CoACD)."""
    hulls = []
    concave_shells: list[SubMesh] = []

    for vertices, triangles in submeshes:
        hull, concave_part = _classify_part(
            vertices, triangles, concavity_tolerance, tiny_hull_volume
        )
        if hull is not None:
            hulls.append(hull)
        if concave_part is None:
            continue

        # Everything below (component split, volume tests, CoACD) scales with
        # input size; a concave prim is only processed in welded resolution.
        vertices, triangles = concave_part
        if len(vertices) > 1000:
            vertices, triangles = _cluster_decimate(vertices, triangles, cell_size)
            if len(vertices) < 4 or not len(triangles):
                continue
        # A concave prim is often many disconnected shells that are convex-ish
        # on their own; splitting keeps most of them off the slow CoACD path.
        for shell_vertices, shell_triangles in _split_connected_components(
            vertices, triangles
        ):
            hull, concave_part = _classify_part(
                shell_vertices,
                shell_triangles,
                concavity_tolerance,
                tiny_hull_volume,
            )
            if hull is not None:
                hulls.append(hull)
            elif concave_part is not None:
                concave_shells.append(concave_part)

    return hulls, concave_shells


def _decompose_concave_shells(concave_shells: list[SubMesh], threshold: float) -> list:
    """CoACD-decompose the shells and return convex hulls of the pieces."""
    import coacd

    coacd.set_log_level("error")

    def decompose(shell: SubMesh):
        vertices, triangles = shell
        return coacd.run_coacd(
            coacd.Mesh(vertices, triangles),
            threshold=threshold,
            # See the tuning block at the top of this file - a visual ghost
            # does not need collision-mesh precision, but the refine build
            # runs in the background now, so these favor fidelity.
            preprocess_resolution=COACD_PREPROCESS_RESOLUTION,
            resolution=COACD_RESOLUTION,
            mcts_nodes=COACD_MCTS_NODES,
            mcts_iterations=COACD_MCTS_ITERATIONS,
            mcts_max_depth=COACD_MCTS_MAX_DEPTH,
            decimate=True,
            max_ch_vertex=COACD_MAX_CH_VERTEX,
        )

    # run_coacd is a ctypes call and releases the GIL, so the independent
    # shells parallelize across threads; each call is single-threaded, so
    # use nearly all cores (this runs in the worker process, not in Kit).
    workers = min(len(concave_shells), max(1, (os.cpu_count() or 4) - 1))
    hulls = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for pieces in pool.map(decompose, concave_shells):
            for piece_vertices, _ in pieces:
                hull = _convex_hull(piece_vertices)
                if hull is not None:
                    hulls.append(hull)
    return hulls


def _union_and_simplify(hulls: list, tolerance: float) -> SubMesh | None:
    """Boolean-union the hulls into one watertight mesh, decimated to tolerance."""
    import manifold3d

    if tolerance > 0:
        # Simplifying the hulls before the union keeps the boolean cheap;
        # the union is simplified once more since it creates new geometry.
        hulls = [hull.simplify(tolerance) for hull in hulls]
        hulls = [hull for hull in hulls if not hull.is_empty()]
    if not hulls:
        return None

    union = manifold3d.Manifold.batch_boolean(hulls, manifold3d.OpType.Add)
    if tolerance > 0:
        union = union.simplify(tolerance)
    if union.status() != manifold3d.Error.NoError or union.is_empty():
        return None

    mesh = union.to_mesh()
    return (
        np.asarray(mesh.vert_properties, dtype=np.float64),
        np.asarray(mesh.tri_verts, dtype=np.int32),
    )


def build_watertight_mesh(
    submeshes: list[SubMesh],
    decompose_threshold: float = DECOMPOSE_THRESHOLD,
    concavity_tolerance: float = CONCAVITY_TOLERANCE,
    simplify_ratio: float = SIMPLIFY_RATIO,
    decompose: bool = True,
) -> SubMesh | None:
    """Union submeshes into a single watertight mesh via convex parts.

    Convex-ish submeshes are replaced by their convex hull (fast). A submesh
    whose hull encloses noticeably more volume than the mesh itself (ratio
    above concavity_tolerance) is concave and gets split into convex pieces
    with CoACD first, so e.g. a C-shaped frame does not turn into a filled
    blob. All convex parts are then boolean-unioned into one watertight
    mesh, which removes internal and overlapping geometry. Finally the mesh
    is decimated with a tolerance of simplify_ratio times the bounding box
    diagonal (0 disables), which collapses the dense vertex rings that hulls
    keep on curved surfaces.

    decompose=False skips the (slow) CoACD step and hulls concave shells
    directly - concave openings fill in, but the result arrives in seconds:
    used for the ghost preview that the refined mesh later replaces.

    Returns (vertices, triangles) or None if the inputs are empty/degenerate.
    """
    submeshes = [submesh for submesh in submeshes if len(submesh[0])]
    if not submeshes:
        return None

    lower = np.min([vertices.min(axis=0) for vertices, _ in submeshes], axis=0)
    upper = np.max([vertices.max(axis=0) for vertices, _ in submeshes], axis=0)
    diagonal = float(np.linalg.norm(upper - lower))
    tolerance = diagonal * simplify_ratio
    # Concave shells below ~1% of the assembly diagonal are visually
    # irrelevant as ghosts; the weld cell tracks the simplify tolerance.
    tiny_hull_volume = (diagonal * 0.01) ** 3
    cell_size = diagonal * max(simplify_ratio, 0.002) * 0.5

    hulls, concave_shells = _collect_convex_hulls(
        submeshes, concavity_tolerance, tiny_hull_volume, cell_size
    )
    if concave_shells and decompose:
        hulls += _decompose_concave_shells(concave_shells, decompose_threshold)
    elif concave_shells:
        hulls += [
            hull
            for vertices, _ in concave_shells
            if (hull := _convex_hull(vertices)) is not None
        ]
    return _union_and_simplify(hulls, tolerance)


def write_submeshes(
    path: str, submeshes: list[SubMesh], decompose: bool = True
) -> None:
    """Serialize submeshes to an .npz file (worker input format).

    The decompose flag travels inside the file rather than on argv, which
    debugger injection may reshuffle (see main).
    """
    arrays = {"count": np.array(len(submeshes)), "decompose": np.array(decompose)}
    for i, (vertices, triangles) in enumerate(submeshes):
        arrays[f"v{i}"] = vertices
        arrays[f"f{i}"] = triangles
    np.savez(path, **arrays)


def read_submeshes(path: str) -> list[SubMesh]:
    """Deserialize submeshes from an .npz file (worker input format)."""
    with np.load(path) as data:
        return [(data[f"v{i}"], data[f"f{i}"]) for i in range(int(data["count"]))]


def main(argv: list[str]) -> int:
    """Worker entry: build_watertight_mesh over an .npz in/out file pair.

    Usage: python watertight_mesh.py <deps-path> <input.npz> <output.npz>
    The output file contains vertices/triangles arrays, or only an "empty"
    marker when the union produced no valid mesh. Only the last two
    arguments are read: debugger injection may reshuffle the front of argv.
    """
    input_path, output_path = argv[-2:]
    with np.load(input_path) as data:
        decompose = bool(data["decompose"]) if "decompose" in data else True
    result = build_watertight_mesh(read_submeshes(input_path), decompose=decompose)
    if result is None:
        np.savez(output_path, empty=np.array(True))
    else:
        vertices, triangles = result
        np.savez(output_path, vertices=vertices, triangles=triangles)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
