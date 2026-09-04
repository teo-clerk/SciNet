/**
 * The Model Lab: what this machine is, what models it holds, who answers
 * which task, and what is on the card right now.
 *
 * Everything here is measured or refused: an unmeasured model shows
 * "unproven" with a Measure button rather than a guessed number, because the
 * failure this panel exists to prevent is silent — Ollama serves an
 * oversized model from system RAM at ~20x with no error at all.
 * Recommendations are advisory; nothing reroutes until the user pins.
 */
import { useEffect, useState } from 'react'

import {
  clearTaskPin,
  fetchModelsOverview,
  requestMeasure,
  setTaskPin,
  type ModelsOverview,
} from '@/api/models'
import { estimateResidentMib, formatMib, gaugeFraction } from '@/lib/modelLab'

/** The JobsDrawer's cadence — measurements land via the worker, the poll
 * picks them up. */
const POLL_MS = 4000

const TASK_LABELS: Record<string, string> = {
  tag_paper: 'Tag papers',
  name_cluster: 'Name regions',
  describe_cluster: 'Region overviews',
  bridge_verdict: 'Bridge verdicts',
  librarian_chat: 'Librarian',
  extract_adjudicate: 'Quantity adjudication',
  insight: 'Core ideas',
}

export function ModelLab() {
  const [data, setData] = useState<ModelsOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [queued, setQueued] = useState<Set<string>>(new Set())

  useEffect(() => {
    let live = true
    const load = () => {
      fetchModelsOverview()
        .then((d) => {
          if (!live) return
          setData(d)
          setError(null)
        })
        .catch((e: unknown) => {
          if (live) setError(e instanceof Error ? e.message : String(e))
        })
    }
    load()
    const timer = setInterval(load, POLL_MS)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [])

  if (error) return <div className="model-lab placeholder">api unreachable — {error}</div>
  if (!data) return <div className="model-lab placeholder">Loading the lab…</div>

  const usedMib = estimateResidentMib(data.resident, data.profiles)
  const fillPct = Math.round(
    gaugeFraction(usedMib, data.hardware.vram_total_mib) * 100,
  )
  const uncatalogued = data.discovered.filter((d) => !d.in_catalog)
  const pinOptions = [
    ...new Set([
      ...data.profiles
        .filter((p) => p.runtime === 'ollama' && p.verdict !== 'cpu-only')
        .map((p) => p.reference),
      ...data.discovered.map((d) => d.reference),
    ]),
  ].sort()

  const measure = (reference: string) => {
    void requestMeasure(reference)
    setQueued((prev) => new Set(prev).add(reference))
  }

  return (
    <div className="model-lab">
      <section className="hardware">
        <h3>
          {data.hardware.gpu_name ?? 'No NVIDIA GPU'}
          {data.hardware.vram_total_mib !== null && (
            <span className="dim"> · {formatMib(data.hardware.vram_total_mib)} VRAM</span>
          )}
        </h3>
        <div className="gauge" title={`~${formatMib(usedMib)} resident of ${formatMib(data.hardware.vram_total_mib)}`}>
          <span className="fill" style={{ width: `${fillPct}%` }} />
        </div>
        <p className="dim">
          {data.resident.length
            ? `on the card now: ${data.resident.join(', ')} (~${formatMib(usedMib)})`
            : 'the card is empty'}
          {' · '}budget {formatMib(data.hardware.vram_budget_mib)} after safety margin
        </p>
      </section>

      <section>
        <h3>Model catalog</h3>
        <table>
          <thead>
            <tr>
              <th>role</th>
              <th>model</th>
              <th>verdict</th>
              <th>vram</th>
              <th>speed</th>
              <th>fits here</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.profiles.map((p) => (
              <tr key={p.reference} title={p.notes ?? ''}>
                <td className="dim">{p.role}</td>
                <td>
                  {p.reference}
                  {data.resident.includes(p.reference) && (
                    <span className="chip ok"> resident</span>
                  )}
                </td>
                <td>
                  <span className={`chip verdict-${p.verdict}`}>{p.verdict}</span>
                </td>
                <td>{formatMib(p.vram_mib)}</td>
                <td className="dim">
                  {p.tok_per_s !== null
                    ? `${p.tok_per_s}${p.runtime === 'huggingface' ? ' docs/s' : ' tok/s'}`
                    : '—'}
                  {p.embed_dim !== null && ` · ${p.embed_dim}d`}
                </td>
                <td>{p.fits_here === null ? '?' : p.fits_here ? 'yes' : 'no'}</td>
                <td>
                  <button
                    className="ghost"
                    disabled={queued.has(p.reference)}
                    onClick={() => measure(p.reference)}
                    title="Warm the model on the worker and record its real footprint"
                  >
                    {queued.has(p.reference) ? 'queued…' : 'Measure'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {uncatalogued.length > 0 && (
        <section>
          <h3>Installed but uncatalogued</h3>
          <p className="dim">
            Found in an Ollama store on this machine — already downloaded,
            never measured. Measuring adds them to the catalog.
          </p>
          <table>
            <tbody>
              {uncatalogued.map((d) => (
                <tr key={`${d.server}:${d.reference}`}>
                  <td>{d.reference}</td>
                  <td className="dim">{d.server}</td>
                  <td className="dim">{formatMib(d.size_mib)} on disk</td>
                  <td className="dim">{d.quantization ?? ''}</td>
                  <td>
                    <button
                      className="ghost"
                      disabled={queued.has(d.reference)}
                      onClick={() => measure(d.reference)}
                    >
                      {queued.has(d.reference) ? 'queued…' : 'Measure'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section>
        <h3>Task routing</h3>
        <p className="dim">
          Pin a task to a model, or leave the default. Nothing reroutes on its
          own — a recommendation is applied by pinning it.
        </p>
        <table>
          <tbody>
            {data.pinnable.map((task) => (
              <tr key={task}>
                <td>{TASK_LABELS[task] ?? task}</td>
                <td>
                  <select
                    value={data.pins[task] ?? ''}
                    onChange={(event) => {
                      const reference = event.target.value
                      void (reference
                        ? setTaskPin(task, reference)
                        : clearTaskPin(task)
                      ).then(() => fetchModelsOverview().then(setData))
                    }}
                  >
                    <option value="">default ({data.tasks[task]})</option>
                    {pinOptions.map((ref) => (
                      <option key={ref} value={ref}>
                        {ref}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="dim">→ {data.tasks[task]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
