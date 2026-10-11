export async function preservePrimaryFailure<T>(
  primaryFailure: unknown,
  finalize: () => Promise<T>,
  recordSecondaryFailure: (error: unknown) => void | Promise<void>,
): Promise<unknown> {
  try {
    await finalize();
    return primaryFailure;
  } catch (secondaryFailure) {
    try { await recordSecondaryFailure(secondaryFailure); } catch { /* A broken reporter must not mask the journey failure. */ }
    return primaryFailure ?? secondaryFailure;
  }
}
