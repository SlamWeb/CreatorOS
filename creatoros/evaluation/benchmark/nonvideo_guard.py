"""Opt-in benchmark dispatch guards; not an operating-system sandbox."""
import json

ALLOWED_CASES = ("B04", "B05", "B06", "B07", "B10", "B11", "B12", "B16", "B22", "B41", "B42", "B44", "B46", "B48", "B49", "B50", "C02", "C04", "D01", "D02", "D04")
POLICY = "nonvideo-no-publication-v1"
COMPARABILITY = "守卫批次与无守卫结果执行条件不同，不能直接等同。"


def policy(case_ids):
    selected = list(case_ids or [])
    if not selected or len(selected) != len(set(selected)) or any(x not in ALLOWED_CASES for x in selected):
        raise ValueError("Nonvideo guard requires an explicit allowed subset; B47 is forbidden")
    return {"id": POLICY, "case_ids": selected, "video_dispatch": "deny", "publication_client": "deny"}


def validate(manifest, case_id=None):
    config = manifest["model_configuration"].get("benchmark_guard")
    if config is None:
        return False
    if config != policy(manifest["selected_case_ids"]):
        raise ValueError("Frozen nonvideo guard policy or case selection differs")
    if case_id is not None and case_id not in config["case_ids"]:
        raise ValueError("Case is outside frozen nonvideo guard selection")
    return True


def deny_video(*args, **kwargs):
    raise RuntimeError("Benchmark nonvideo guard: video production is forbidden")


class DenyPublication:
    """Fail closed for all platform client operations, without opening credentials."""
    def __getattr__(self, name):
        async def denied(*args, **kwargs):
            raise RuntimeError("Benchmark nonvideo guard: platform operations are forbidden")
        return denied


def install_video_guard():
    from creatoros.integrations import video_production
    video_production.produce_video = deny_video
    if video_production.produce_video is not deny_video:
        raise RuntimeError("Video guard installation failed")


def evidence(manifest, case_id, root):
    """Check the saved installation receipt without modifying raw evidence."""
    configured = manifest.get("model_configuration", {}).get("benchmark_guard")
    if configured is None:
        return None
    result = {"policy": configured, "comparability": COMPARABILITY, "status": "failed"}
    try:
        validate(manifest, case_id)
        receipt = json.loads((root / "guard.json").read_text(encoding="utf-8"))
        expected = {"policy": configured, "case_id": case_id, "video_dispatch_installed": True,
                    "publication_client_installed": True, "network_sandbox": False}
        if receipt != expected:
            raise ValueError("guard receipt does not match frozen policy and case")
    except (OSError, ValueError, TypeError, KeyError) as error:
        result["error"] = type(error).__name__
    else:
        result["status"] = "passed"
    return result
