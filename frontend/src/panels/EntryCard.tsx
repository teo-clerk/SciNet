/**
 * The floating answer to "where do I start?", raised from the filter bar.
 *
 * Top-left, under the bar that asked the question, and beside the region
 * inspector rather than under it when both are open (the CSS handles that
 * with a sibling selector — the two are adjacent in the DOM for the purpose).
 * Cleared with the filters, because it was an answer about the filtered set.
 */
import { EntryPointCard } from '@/panels/EntryPointCard'
import { useReadingOrder } from '@/lib/useReadingOrder'
import { useGraphStore } from '@/state/graphStore'

export function EntryCard() {
  const card = useGraphStore((s) => s.entryCard)
  const setEntryCard = useGraphStore((s) => s.setEntryCard)
  const nodes = useGraphStore((s) => s.nodes)
  const setSelected = useGraphStore((s) => s.setSelected)
  const setView = useGraphStore((s) => s.setView)
  const startReadingOrder = useReadingOrder()

  if (!card) return null

  const open = (paperId: number) => {
    const index = nodes.findIndex((n) => n.id === paperId)
    if (index < 0) return
    setView('map')
    setSelected(index)
  }

  return (
    <aside className="entry-card">
      <button className="close" aria-label="Close" onClick={() => setEntryCard(null)}>
        ×
      </button>
      <EntryPointCard
        entry={card.entry}
        alternatives={card.alternatives}
        readingOrder={card.readingOrder}
        onOpen={open}
        onReadingOrder={() => startReadingOrder(card.readingOrder)}
      />
    </aside>
  )
}
