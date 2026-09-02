/**
 * Model Lab arithmetic, kept apart from the component so it can be tested
 * without rendering anything (the house pattern).
 */
import type { ModelProfileInfo } from '@/api/models'

/** Human MiB: whole G for models, raw MiB below one. */
export function formatMib(mib: number | null): string {
  if (mib === null) return '?'
  if (mib >= 1024) return `${(mib / 1024).toFixed(2)}G`
  return `${mib}M`
}

/**
 * What the resident models are believed to hold, from their measured
 * footprints. A resident model with no measurement contributes zero — the
 * gauge understates rather than invents, and the "unproven" badge next to
 * the model says why.
 */
export function estimateResidentMib(
  resident: string[],
  profiles: ModelProfileInfo[],
): number {
  const byRef = new Map(profiles.map((p) => [p.reference, p.vram_mib]))
  return resident.reduce((sum, ref) => sum + (byRef.get(ref) ?? 0), 0)
}

/** The gauge's fill fraction, clamped — an overfull card still reads 100%. */
export function gaugeFraction(usedMib: number, totalMib: number | null): number {
  if (!totalMib || totalMib <= 0) return 0
  return Math.min(1, usedMib / totalMib)
}
