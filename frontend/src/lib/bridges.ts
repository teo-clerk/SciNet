/**
 * The bridges that touch one region, seen from that region.
 *
 * `/api/clusters` returns every link once, as a source/target pair. An
 * inspector open on one region wants the same links with "here" and "there"
 * resolved, strongest first — which end is the source is an accident of how
 * the pair was stored, not something the reader should have to think about.
 */
import type { ClusterLink } from '@/api/clusters'

export interface BridgeFromHere {
  otherId: number
  otherLabel: string | null
  similarity: number
  summary: string | null
}

export function bridgesFrom(
  links: readonly ClusterLink[],
  clusterId: number,
): BridgeFromHere[] {
  return links
    .filter((link) => link.source_id === clusterId || link.target_id === clusterId)
    .map((link) => {
      const here = link.source_id === clusterId
      return {
        otherId: here ? link.target_id : link.source_id,
        otherLabel: here ? link.target_label : link.source_label,
        similarity: link.similarity,
        summary: link.summary,
      }
    })
    .sort((a, b) => b.similarity - a.similarity)
}
