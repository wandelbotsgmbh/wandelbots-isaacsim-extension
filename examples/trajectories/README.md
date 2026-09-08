# Trajectory plan → runnable NOVA programs

`generate_trajectories.py` turns each Isaac Sim trajectory plan exported to the
NOVA object store (`trajectory-plan/*`) into a self-contained, directly
executable, fully editable NOVA program `skills/skill_<name>.py` — poses,
joints, TCP offsets, mounting and limits are plain Python literals, no JSON.

## Setup

```bash
cd examples/trajectories
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
source .venv/bin/activate
```

Provide the NOVA connection via flags or environment variables (a local `.env`
is loaded automatically):

| Setting | Env var | Flag | Default |
| --- | --- | --- | --- |
| Host / base URL | `NOVA_API` / `NOVA_HOST` | `--host` | – (required) |
| Cell id | `CELL` | `--cell` | `cell` |
| Access token | `NOVA_ACCESS_TOKEN` | `--token` | – (optional) |
| Output dir | – | `--out` | `./skills` |

## Generate

```bash
python generate_trajectories.py --host http://<nova-host> --cell cell
```

This lists every `trajectory-plan/*` object (the `trajectory-plan-config/*`
companions are skipped), resolves the stored robot model to a virtual-controller
type via `GET /api/v2/robot-configurations`, and writes one
`skills/skill_<name>.py` per plan.

## Run a trajectory

```bash
cd skills
python skill_<name>.py
```

Each program:

1. creates a **virtual controller** from the stored robot model (via
   `ProgramPreconditions`),
2. registers the trajectory's custom TCP(s) and base mounting,
3. moves the (home) robot to the trajectory's taught start joints,
4. replays the taught motion with `MotionGroup.plan_and_execute`.

Connection for running a program uses the standard `wandelbots-nova` env vars
(`NOVA_API`, `NOVA_ACCESS_TOKEN`, `NOVA_CELL`).

## What gets generated

| Payload type | Generated as |
| --- | --- |
| `plan_trajectory` (single TCP) | one `actions = [cartesian_ptp/linear/joint_ptp(...)]` list |
| `plan_trajectory` (segmented, multi-TCP) | one action list per segment, executed sequentially |
| `plan_collision_free` | a `JOINT_TARGETS` list replayed as `joint_ptp` moves |

## Known limitations (high-level replay is intentionally lossy)

- Per-command blending / limit overrides are approximated by the planner;
  inter-segment blending of multi-TCP plans is dropped.
- Collision-free skills only store start/target joint pairs, so they are
  replayed as `joint_ptp` moves **without re-running collision avoidance**.
- `PathCircle` / `PathCubicSpline` commands are skipped with a warning.
