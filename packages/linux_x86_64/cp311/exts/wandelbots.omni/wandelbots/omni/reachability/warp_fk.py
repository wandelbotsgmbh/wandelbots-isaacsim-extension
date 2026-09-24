"""GPU forward-kinematics sweep for the reachability envelope.

Ports the DH forward kinematics of ``manipulators/utils.compute_forward_kinematics_chain``
to an NVIDIA Warp kernel, so a few hundred thousand joint-space samples can be
evaluated at once and reduced to the voxels the arm's TCP can pass through.

This is the cheap, local half of the tool and it is deliberately approximate: it
says where the arm can reach in *some* configuration, which the overlay draws as
a likelihood spectrum. Whether a particular pose is actually reachable is a
separate question, answered by a single NOVA inverse-kinematics call per placed
pose - never from here.

Falls back to a vectorised numpy implementation when Warp or a CUDA device is
missing, so the feature degrades instead of disappearing.
"""

from __future__ import annotations

import carb
import numpy as np

from wandelbots.omni.reachability.dh_chain import DHChain

try:
    import warp as wp

    WARP_AVAILABLE = True
except Exception:  # pragma: no cover - Warp only present inside Kit
    wp = None
    WARP_AVAILABLE = False


def sample_joint_space(chain: DHChain, num_samples: int, seed: int = 0) -> np.ndarray:
    """Uniform random joint samples within limits, shape (num_samples, J) float32."""
    rng = np.random.default_rng(seed)
    lo = chain.lower[None, :]
    hi = chain.upper[None, :]
    return (lo + (hi - lo) * rng.random((num_samples, chain.num_joints))).astype(
        np.float32
    )


# --------------------------------------------------------------------------- #
# Warp kernel
# --------------------------------------------------------------------------- #
if WARP_AVAILABLE:

    @wp.kernel
    def _fk_kernel(
        n_joints: int,
        a: wp.array(dtype=wp.float32),
        alpha: wp.array(dtype=wp.float32),
        d: wp.array(dtype=wp.float32),
        theta0: wp.array(dtype=wp.float32),
        sign: wp.array(dtype=wp.float32),
        q: wp.array2d(dtype=wp.float32),
        tcp: wp.mat44,
        out_pos: wp.array2d(dtype=wp.float32),
    ):
        tid = wp.tid()
        t = wp.identity(n=4, dtype=wp.float32)
        for j in range(n_joints):
            th = sign[j] * q[tid, j] + theta0[j]
            ct = wp.cos(th)
            st = wp.sin(th)
            ca = wp.cos(alpha[j])
            sa = wp.sin(alpha[j])
            aj = a[j]
            dj = d[j]
            tj = wp.mat44(
                ct,
                -st * ca,
                st * sa,
                aj * ct,
                st,
                ct * ca,
                -ct * sa,
                aj * st,
                0.0,
                sa,
                ca,
                dj,
                0.0,
                0.0,
                0.0,
                1.0,
            )
            t = t * tj
        t = t * tcp
        out_pos[tid, 0] = t[0, 3]
        out_pos[tid, 1] = t[1, 3]
        out_pos[tid, 2] = t[2, 3]


def _tcp_mat(tcp_offset_mm: np.ndarray | None) -> np.ndarray:
    if tcp_offset_mm is None:
        return np.eye(4, dtype=np.float32)
    return tcp_offset_mm.astype(np.float32)


def _fk_numpy(chain: DHChain, q: np.ndarray, tcp: np.ndarray) -> np.ndarray:
    """Vectorised numpy DH FK over all samples. Returns (N, 3) positions in mm."""
    n = q.shape[0]
    acc = np.broadcast_to(np.eye(4, dtype=np.float32), (n, 4, 4)).copy()
    for j in range(chain.num_joints):
        th = chain.sign[j] * q[:, j] + chain.theta0[j]
        ct, st = np.cos(th), np.sin(th)
        ca, sa = np.cos(chain.alpha[j]), np.sin(chain.alpha[j])
        aj, dj = chain.a[j], chain.d[j]
        tj = np.zeros((n, 4, 4), dtype=np.float32)
        tj[:, 0, 0] = ct
        tj[:, 0, 1] = -st * ca
        tj[:, 0, 2] = st * sa
        tj[:, 0, 3] = aj * ct
        tj[:, 1, 0] = st
        tj[:, 1, 1] = ct * ca
        tj[:, 1, 2] = -ct * sa
        tj[:, 1, 3] = aj * st
        tj[:, 2, 1] = sa
        tj[:, 2, 2] = ca
        tj[:, 2, 3] = dj
        tj[:, 3, 3] = 1.0
        acc = acc @ tj
    acc = acc @ tcp[None, :, :]
    return acc[:, :3, 3]


def fk_sweep_positions(
    chain: DHChain,
    q: np.ndarray,
    tcp_offset_mm: np.ndarray | None = None,
) -> np.ndarray:
    """Forward-kinematics for a batch of joint samples.

    Args:
        chain: DH chain.
        q: (N, J) joint samples [rad].
        tcp_offset_mm: optional 4x4 flange->TCP transform (mm), applied after the chain.

    Returns:
        (N, 3) TCP positions in the base (link_0) frame, in millimetres.
    """
    tcp = _tcp_mat(tcp_offset_mm)
    q = np.ascontiguousarray(q, dtype=np.float32)

    if not (WARP_AVAILABLE and _cuda_device()):
        return chain.to_mount_frame(_fk_numpy(chain, q, tcp))

    try:
        device = "cuda"
        n = q.shape[0]
        out = wp.zeros((n, 3), dtype=wp.float32, device=device)
        tcp_wp = wp.mat44(*tcp.reshape(-1).tolist())
        wp.launch(
            _fk_kernel,
            dim=n,
            inputs=[
                chain.num_joints,
                wp.array(chain.a, dtype=wp.float32, device=device),
                wp.array(chain.alpha, dtype=wp.float32, device=device),
                wp.array(chain.d, dtype=wp.float32, device=device),
                wp.array(chain.theta0, dtype=wp.float32, device=device),
                wp.array(chain.sign, dtype=wp.float32, device=device),
                wp.array(q, dtype=wp.float32, device=device),
                tcp_wp,
                out,
            ],
            device=device,
        )
        wp.synchronize()
        return chain.to_mount_frame(out.numpy())
    except Exception as exc:  # fall back rather than break the overlay
        carb.log_warn(f"Warp FK sweep failed ({exc}); using numpy fallback")
        return chain.to_mount_frame(_fk_numpy(chain, q, tcp))


def _cuda_device() -> bool:
    if not WARP_AVAILABLE:
        return False
    try:
        return wp.get_device().is_cuda
    except Exception:
        return False


def voxelize(positions: np.ndarray, voxel_mm: float) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a point set to occupied voxel centres.

    Returns (centres (M,3) mm, counts (M,)) — counts approximate dexterity/density.

    Uses a packed 1-D integer key instead of ``np.unique(axis=0)``; unique over a
    flat int64 array is markedly faster than the lexicographic 2-D variant for the
    hundreds of thousands of samples we sweep.
    """
    if positions.size == 0:
        return np.empty((0, 3), np.float32), np.empty((0,), np.int64)
    idx = np.floor(positions / voxel_mm).astype(np.int64)
    # Pack (i, j, k) into one int64. Offset keeps values non-negative; 21 bits per
    # axis covers +/-1e6 voxels — far beyond any real robot reach / voxel size.
    offset = np.int64(1 << 20)
    key = (
        ((idx[:, 0] + offset) << np.int64(42))
        | ((idx[:, 1] + offset) << np.int64(21))
        | (idx[:, 2] + offset)
    )
    _, first_idx, counts = np.unique(key, return_index=True, return_counts=True)
    centres = (idx[first_idx].astype(np.float32) + 0.5) * voxel_mm
    return centres, counts


def gpu_backend() -> str:
    """Human-readable compute backend, for logging/UI."""
    if WARP_AVAILABLE and _cuda_device():
        return f"warp-cuda ({wp.get_device()})"
    if WARP_AVAILABLE:
        return "warp-cpu/numpy"
    return "numpy"
