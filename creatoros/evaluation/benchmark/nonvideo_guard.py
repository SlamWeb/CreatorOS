"""Opt-in benchmark dispatch guards; not an operating-system sandbox."""

ALLOWED_CASES = ("B04", "B41", "B42", "B44", "B46", "B48", "B49", "B50")
POLICY = "nonvideo-no-publication-v1"


def policy(case_ids):
    selected = list(case_ids or [])
    if not selected or len(selected) != len(set(selected)) or any(x not in ALLOWED_CASES for x in selected):
        raise ValueError("Nonvideo guard requires an explicit subset of the eight allowed cases; B47 is forbidden")
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
