"""Errors shared by production callers without loading the executor."""


class ContentRunError(ValueError):
    """A requested ContentRun transition is invalid."""

    def __init__(self, message: str, *, code: str = "conflict", status_code: int = 409,
                 run_id: str | None = None, iteration_id: str | None = None):
        super().__init__(message)
        self.code, self.status_code, self.run_id = code, status_code, run_id
        self.iteration_id = iteration_id


class ContentRunExecutionError(RuntimeError):
    def __init__(self, run_id: str, message: str):
        super().__init__(message)
        self.run_id = run_id


class ContentRunLeaseError(ContentRunError):
    """The caller no longer owns the run and must not write a late result."""
