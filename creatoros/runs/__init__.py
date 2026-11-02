"""Public production API; load implementation only when a caller needs it."""

from importlib import import_module

from .errors import ContentRunError, ContentRunExecutionError, ContentRunLeaseError

__all__ = [
    "ArtifactValidation",
    "ContentRunError",
    "ContentRunExecutionError",
    "ContentRunLeaseError",
    "ContentRunInput",
    "ContentRunCLI",
    "ContentRunRepository",
    "ContentRunService",
    "ManagedRunExecutor",
    "ReferenceProductionInput",
    "ReferenceProductionService",
    "RunExecutionResult",
    "RunSubmission",
    "ValidatedImage",
    "validate_artifact",
]

_EXPORT_MODULE = {
    "ArtifactValidation": ".models",
    "ContentRunInput": ".models",
    "ContentRunCLI": ".cli",
    "ContentRunRepository": ".repository",
    "ContentRunService": ".service",
    "ManagedRunExecutor": ".executor",
    "ReferenceProductionInput": ".reference_production",
    "ReferenceProductionService": ".reference_production",
    "RunExecutionResult": ".models",
    "RunSubmission": ".executor",
    "ValidatedImage": ".models",
    "validate_artifact": ".artifacts",
}


def __getattr__(name: str):
    module_name = _EXPORT_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
