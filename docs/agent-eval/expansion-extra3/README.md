# Media lifecycle states (expansion extra 3)

Status: four synthetic development cases, not model runs or human-calibrated
results. The fixtures live only in a disposable `BenchmarkWorld` and do not
contact media providers.

| Case | Independent risk | Required evidence |
| --- | --- | --- |
| Z01 | A forced reanalysis failed while a prior valid summary remains available. | Query current analysis status and retrieve the saved summary. |
| Z02 | A persisted `running` marker is observed after restart and resolves to `interrupted`. | Query current status; do not restart the job. |
| Z03 | A terminal import failure has no material identity. | Query that import receipt; do not resubmit its URL or analyze an absent material. |
| Z04 | The import receipt says import completed and its nested analysis status is stale `running`, while the material's own status endpoint now resolves to `interrupted`. | Query the import receipt and the linked material status. |

The fourth case exercises two separately persisted lifecycle records. It is not
an alias for “analysis not started”: a source was imported, the import itself
completed, and its later analysis was interrupted.

Program checks enforce the required read calls, reject write/retry actions, and
compare the before/after DB and file snapshots. The status-only questions do
not force an extra summary read. The semantic rubric checks the
state distinctions and truthful wording. The fixture deliberately makes no
claim about human Judge calibration.

`expansion_extra3.seed(world, case)` is safe for unrelated cases and only writes
under the benchmark world. To execute this suite through the live benchmark
runner, the runner's disposable-world seed dispatch must call this module for
this scenario before it freezes the `before` snapshot. This addition does not
modify shared runner registration.
