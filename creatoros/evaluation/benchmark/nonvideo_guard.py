"""Opt-in benchmark dispatch guards; not an operating-system sandbox."""
import json

ALLOWED_CASES = ("B04", "B05", "B06", "B07", "B10", "B11", "B12", "B16", "B22", "B41", "B42", "B44", "B46", "B48", "B49", "B50", "C02", "C04", "D01", "D02", "D04", "E02", "E05", "M01", "M04", "S01", "S04", "P03", "W01", "I02")
POLICY = "nonvideo-no-publication-v1"
COMPARABILITY = "守卫批次与无守卫结果执行条件不同，不能直接等同。"


def policy(case_ids, *, local_dispatch=False, source_namespace=None):
    selected = list(case_ids or [])
    from .local_dispatch_guard import CASES_BY_NAMESPACE, CASES_BY_VERSION, VERSION, VERSIONS
    version = VERSION if local_dispatch is True else local_dispatch
    if version and version not in VERSIONS:
        raise ValueError("Unknown local dispatch policy")
    if version in CASES_BY_NAMESPACE:
        if source_namespace not in CASES_BY_NAMESPACE[version]:
            raise ValueError("v4 requires an authorized source namespace")
        allowed = CASES_BY_NAMESPACE[version][source_namespace]
    else:
        if source_namespace is not None:
            raise ValueError("Namespace applies only to v4")
        allowed = CASES_BY_VERSION[version] if local_dispatch else ALLOWED_CASES
    if not selected or len(selected) != len(set(selected)) or any(x not in allowed for x in selected):
        raise ValueError("Nonvideo guard requires an explicit allowed subset; B47 is forbidden")
    result = {"id": POLICY, "case_ids": selected, "video_dispatch": "deny", "publication_client": "deny"}
    if local_dispatch:
        result["local_dispatch"] = version
    if version in CASES_BY_NAMESPACE:
        result["source_namespace"] = source_namespace
    return result


def validate(manifest, case_id=None):
    config = manifest["model_configuration"].get("benchmark_guard")
    if config is None:
        return False
    from .local_dispatch_guard import VERSIONS
    local_dispatch = config.get("local_dispatch", False)
    if local_dispatch and local_dispatch not in VERSIONS:
        raise ValueError("Unknown local dispatch policy")
    if config != policy(manifest["selected_case_ids"], local_dispatch=local_dispatch,
                        source_namespace=config.get("source_namespace")):
        raise ValueError("Frozen nonvideo guard policy or case selection differs")
    if local_dispatch in {"local-dispatch-v4", "local-dispatch-v5", "local-dispatch-v6", "local-dispatch-v7"}:
        from creatoros.evaluation.regression_runner import selection_namespace
        from creatoros.evaluation.tool_calling import E06_SCOPE_SUITE_ID, EXPANSION_SUITE_ID, NATURAL_SUITE_ID, REGRESSION_SUITE_ID
        binding = manifest["model_configuration"].get("catalog_binding")
        if not isinstance(binding, dict):
            raise ValueError("v4 requires a frozen catalog binding")
        selected = binding.get("selected_instance_ids")
        namespace = selection_namespace(selected)
        if (namespace != config["source_namespace"]
                or [item.split(":", 1)[1] for item in selected] != manifest["selected_case_ids"]
                or manifest.get("dataset_id") != {"base": REGRESSION_SUITE_ID, "natural": NATURAL_SUITE_ID, "expansion": EXPANSION_SUITE_ID, "e06-scope": E06_SCOPE_SUITE_ID}[namespace]):
            raise ValueError("Frozen source namespace and dataset differ")
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
        if configured.get("local_dispatch"):
            expected.update(local_dispatch_installed=True, background_services_deferred=True)
        if receipt != expected:
            raise ValueError("guard receipt does not match frozen policy and case")
    except (OSError, ValueError, TypeError, KeyError) as error:
        result["error"] = type(error).__name__
    else:
        result["status"] = "passed"
    return result
