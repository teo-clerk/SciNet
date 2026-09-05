/**
 * A library to try, for someone who has nothing to drop in yet.
 *
 * Two kinds of card. A bundled sample installs with one click. A fetched one
 * shows the command the reader runs themselves — the application does not
 * download anything, ever, and the card says so in as many words, because
 * that sentence is the whole privacy story and it should be read here first.
 */
import { useEffect, useState } from 'react'

import { fetchSamples, installSample, type Sample } from '@/api/samples'
import { useCopy } from '@/lib/useCopy'

const EGRESS_NOTE =
  'SciNet never downloads anything itself; this script runs on your machine, ' +
  "checks every hash, and leaves the app's egress log empty."

export function SampleCards() {
  const [samples, setSamples] = useState<Sample[]>([])

  useEffect(() => {
    let alive = true
    fetchSamples()
      .then((list) => alive && setSamples(list))
      // No samples endpoint, no cards: the drop zone above still works.
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [])

  if (samples.length === 0) return null

  return (
    <div className="sample-cards">
      {samples.map((sample) =>
        sample.kind === 'bundled' ? (
          <BundledCard key={sample.name} sample={sample} />
        ) : (
          <FetchCard key={sample.name} sample={sample} />
        ),
      )}
    </div>
  )
}

function BundledCard({ sample }: { sample: Sample }) {
  const [note, setNote] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [installed, setInstalled] = useState(sample.installed)

  const install = async () => {
    setBusy(true)
    try {
      const result = await installSample(sample.name)
      setInstalled(true)
      setNote(
        result.queued > 0
          ? `${result.queued} ${result.queued === 1 ? 'work' : 'works'} queued`
          : 'already in the library',
      )
    } catch (error: unknown) {
      setNote(error instanceof Error ? error.message : String(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <article className="sample-card">
      <h3>{sample.title}</h3>
      <p className="prose">{sample.blurb}</p>
      <div className="row">
        <span className="dim">{sample.documents} works</span>
        <button onClick={install} disabled={installed || busy}>
          {installed ? 'Installed' : busy ? 'Installing…' : 'Install'}
        </button>
      </div>
      {note && <p className="dim note">{note}</p>}
    </article>
  )
}

function FetchCard({ sample }: { sample: Sample }) {
  const { copied, copy } = useCopy()
  const command = sample.command

  return (
    <article className="sample-card">
      <h3>{sample.title}</h3>
      <p className="prose">{sample.blurb}</p>
      <div className="row">
        <span className="dim">{sample.documents} works</span>
        {sample.installed && <span className="chip">Installed</span>}
      </div>
      {command && (
        <div className="command">
          <code>{command}</code>
          <button onClick={() => void copy(command)}>{copied ? 'Copied' : 'Copy'}</button>
        </div>
      )}
      <p className="dim note">{EGRESS_NOTE}</p>
    </article>
  )
}
