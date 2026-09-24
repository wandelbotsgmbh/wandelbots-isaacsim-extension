import omni.graph.core as og
import omni.timeline
import omni.usd

from wandelbots.omni.utils.contact_gripper import ContactGripperModel


class OgnContactGripper:
    @staticmethod
    def internal_state() -> ContactGripperModel:
        return ContactGripperModel()

    @staticmethod
    def compute(db) -> bool:
        model: ContactGripperModel = db.per_instance_state
        stick = bool(db.inputs.stick)
        fix_all = bool(db.inputs.fixAll)

        stick_changed = stick != model.prev_stick
        model.prev_stick = stick
        # With Fix All the scan repeats on every execution while Fix is true,
        # so prims that enter the volume later get attached as well.
        if not stick_changed and not (stick and fix_all):
            OgnContactGripper._write_state_outputs(db, model)
            return True

        stage = omni.usd.get_context().get_stage()
        if stage is None:
            db.log_error("No USD stage is available.")
            return False

        timeline = omni.timeline.get_timeline_interface()
        is_playing = timeline.is_playing() if timeline is not None else True
        if not is_playing:
            model.restore_all()
            OgnContactGripper._write_state_outputs(db, model)
            return True

        helper_paths = OgnContactGripper._extract_target_paths(db.inputs.helperPrim)
        helper_path = helper_paths[0] if helper_paths else ""
        if not helper_path:
            db.log_error("inputs:helperPrim is required.")
            return False

        helper_prim = stage.GetPrimAtPath(helper_path)
        if not helper_prim.IsValid():
            db.log_error(f"Helper prim does not exist: {helper_path}")
            return False

        event_attached = False
        event_released = False

        if not stick:
            model.helper_prim_path = helper_path
            event_released = model.release()
        else:
            event_attached = model.attach(
                helper_path=helper_path,
                candidate_paths=OgnContactGripper._normalize_token_list(
                    db.inputs.candidatePrimPaths
                ),
                exclude_paths=OgnContactGripper._normalize_token_list(
                    db.inputs.excludePrimPaths
                ),
                attach_all=fix_all,
            )

        OgnContactGripper._write_state_outputs(db, model)
        OgnContactGripper._set_exec_outputs(db, event_attached, event_released)
        return True

    @staticmethod
    def _write_state_outputs(db, model: ContactGripperModel) -> None:
        db.outputs.isAttached = model.is_attached
        db.outputs.attachedPrimPath = model.attached_prim_path
        db.outputs.attachedPrimPaths = model.attached_prim_paths

    @staticmethod
    def _set_exec_outputs(db, event_attached: bool, event_released: bool) -> None:
        if hasattr(db.outputs, "execAttached") and event_attached:
            db.outputs.execAttached = og.ExecutionAttributeState.ENABLED
        if hasattr(db.outputs, "execReleased") and event_released:
            db.outputs.execReleased = og.ExecutionAttributeState.ENABLED

    @staticmethod
    def _normalize_token_list(token_list) -> list[str]:
        return [str(t).strip() for t in token_list if str(t).strip()]

    @staticmethod
    def _extract_target_paths(target_value) -> list[str]:
        if target_value is None:
            return []

        paths = getattr(target_value, "paths", None)
        if paths is not None:
            try:
                return [str(p).strip() for p in paths if str(p).strip()]
            except TypeError:
                pass

        if isinstance(target_value, (list, tuple)):
            return [str(p).strip() for p in target_value if str(p).strip()]

        value = str(target_value).strip()
        if not value or value in {"[]", "None"}:
            return []
        return [value]
