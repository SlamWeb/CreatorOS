// A zero Playwright exit is not full coverage if cases were skipped or retried.
export function classifyGroup({ exitCode, launchError, expected, unexpected, skipped, flaky,
  missing_specs, unexpected_specs, errors }) {
  if (launchError || exitCode !== 0 || unexpected || missing_specs.length || unexpected_specs.length || errors.length) return "failed";
  if (!expected || skipped) return "incomplete";
  if (flaky) return "flaky";
  return "passed";
}
