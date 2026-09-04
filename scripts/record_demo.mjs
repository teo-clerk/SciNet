/**
 * Scripted demo recorder for the 3D map.
 *
 * A README GIF that was captured by hand cannot be re-captured: the corpus
 * grew, the cursor wandered, the pacing changed, and re-doing it costs an
 * afternoon nobody spends — so the asset rots. This harness makes each demo a
 * *scenario file* (a list of timed actions) driven over CDP, the same way the
 * verify_*.mjs gates work, so any demo can be re-recorded against any corpus
 * with one command.
 *
 *   bun scripts/record_demo.mjs scripts/scenarios/smoke.mjs [--url URL]
 *       [--out DIR] [--headful]
 *
 * Frames arrive via Page.startScreencast with real timestamps; assembly uses
 * ffmpeg's concat demuxer with per-frame durations, so the recording keeps the
 * app's true pacing rather than a guessed constant framerate. Without ffmpeg
 * the frames are kept and the assembly command is printed.
 *
 * A scenario module default-exports:
 *   { name, url?, steps: [...] }
 * with steps drawn from: { wait: 'css', timeout? } · { waitGone: 'css', timeout? }
 * · { sleep: ms } · { click: 'css' } · { clickAt: [x, y] } · { hover: [x, y] } ·
 * { orbit: { x, y, steps, dx } } · { type: 'text' } · { evaluate: 'js' } ·
 * { startRecording } · { stopRecording }.
 * Waits gate on real state; sleeps only pace the cut.
 */
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

const args = process.argv.slice(2)
const scenarioPath = args.find((a) => !a.startsWith('--'))
if (!scenarioPath) {
  console.error('usage: bun scripts/record_demo.mjs <scenario.mjs> [--url URL] [--out DIR] [--headful]')
  process.exit(1)
}
const flag = (name, fallback) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const HEADFUL = args.includes('--headful')
const URL_ARG = flag('--url', 'http://localhost:5173')
// 9225: the verify_* gates already claim 9222-9224, and sharing a debugging
// port with a gate that may be running is a confusing way to record it.
const PORT = 9225
const WIDTH = 1600
const HEIGHT = 900

const scenario = (await import(resolve(scenarioPath))).default
const OUT_DIR = resolve(flag('--out', `/tmp/scinet-demo/${scenario.name}`))
mkdirSync(`${OUT_DIR}/frames`, { recursive: true })

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function launchChromium() {
  const chromiumArgs = [
    `--remote-debugging-port=${PORT}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--user-data-dir=/tmp/scinet-demo-profile',
    `--window-size=${WIDTH},${HEIGHT + 100}`,
    '--enable-gpu',
    '--ignore-gpu-blocklist',
    'about:blank',
  ]
  if (!HEADFUL) chromiumArgs.unshift('--headless=new')
  return Bun.spawn(['chromium', ...chromiumArgs], { stdout: 'ignore', stderr: 'ignore' })
}

async function cdpTarget() {
  // Attach to the browser's INITIAL tab rather than opening a second one:
  // headless=new refuses Page.startScreencast on any tab but the active
  // first one, with an error that names none of this ("Not attached to an
  // active page"). The verify_* gates open fresh tabs because they never
  // screencast; this harness must not copy that part of the pattern.
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      if (res.ok) {
        const page = (await res.json()).find((t) => t.type === 'page')
        if (page) return page
      }
    } catch {
      /* browser not up yet */
    }
    await sleep(250)
  }
  throw new Error('chromium devtools endpoint never came up')
}

/** The verify_*.mjs Cdp client, plus live event handlers — the screencast
 * must be acked as frames arrive or Chromium stops sending them. */
class Cdp {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.handlers = new Map()
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id)
        if (p) {
          this.pending.delete(msg.id)
          msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result)
        }
      } else {
        this.handlers.get(msg.method)?.(msg.params)
      }
    })
  }

  on(method, handler) {
    this.handlers.set(method, handler)
  }

  send(method, params = {}) {
    const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      setTimeout(() => {
        if (this.pending.delete(id)) reject(new Error(`${method} timed out`))
      }, 30_000)
    })
  }
}

async function connect(wsUrl) {
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true })
    ws.addEventListener('error', rej, { once: true })
  })
  return new Cdp(ws)
}

// --- the step vocabulary ----------------------------------------------------

async function waitFor(cdp, selector, timeout = 20_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const probe = await cdp.send('Runtime.evaluate', {
      expression: `!!document.querySelector(${JSON.stringify(selector)})`,
      returnByValue: true,
    })
    if (probe.result.value) return
    await sleep(250)
  }
  throw new Error(`"${selector}" never appeared (${timeout} ms)`)
}

/** The complement of waitFor: a spinner, a warming notice, a panel closing.
 * Gating on the element's absence is what makes "results landed" a real
 * state rather than a guessed sleep. */
async function waitGone(cdp, selector, timeout = 20_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const probe = await cdp.send('Runtime.evaluate', {
      expression: `!document.querySelector(${JSON.stringify(selector)})`,
      returnByValue: true,
    })
    if (probe.result.value) return
    await sleep(250)
  }
  throw new Error(`"${selector}" never went away (${timeout} ms)`)
}

async function centerOf(cdp, selector) {
  const probe = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector(${JSON.stringify(selector)})
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: r.x + r.width / 2, y: r.y + r.height / 2 }
    })()`,
    returnByValue: true,
  })
  if (!probe.result.value) throw new Error(`"${selector}" not found to click`)
  return probe.result.value
}

async function mouse(cdp, type, x, y, extra = {}) {
  await cdp.send('Input.dispatchMouseEvent', { type, x, y, ...extra })
}

async function clickAt(cdp, x, y) {
  // A hover beat first: the map's picker needs to see the cursor arrive
  // before the press, exactly as a human click does.
  await mouse(cdp, 'mouseMoved', x, y)
  await sleep(150)
  await mouse(cdp, 'mousePressed', x, y, { button: 'left', clickCount: 1 })
  await sleep(40)
  await mouse(cdp, 'mouseReleased', x, y, { button: 'left', clickCount: 1 })
}

async function orbit(cdp, { x = 500, y = 450, steps = 40, dx = 12 } = {}) {
  await mouse(cdp, 'mousePressed', x, y, { button: 'left', clickCount: 1 })
  for (let i = 0; i < steps; i++) {
    await mouse(cdp, 'mouseMoved', x + i * dx, y, { button: 'left' })
    await sleep(16)
  }
  await mouse(cdp, 'mouseReleased', x + steps * dx, y, { button: 'left', clickCount: 1 })
}

async function runStep(cdp, step, recorder) {
  if (step.wait) return waitFor(cdp, step.wait, step.timeout)
  if (step.waitGone) return waitGone(cdp, step.waitGone, step.timeout)
  if (step.sleep) return sleep(step.sleep)
  if (step.click) {
    const { x, y } = await centerOf(cdp, step.click)
    return clickAt(cdp, x, y)
  }
  if (step.clickAt) return clickAt(cdp, step.clickAt[0], step.clickAt[1])
  if (step.hover) return mouse(cdp, 'mouseMoved', step.hover[0], step.hover[1])
  if (step.orbit) return orbit(cdp, step.orbit)
  if (step.type) return cdp.send('Input.insertText', { text: step.type })
  if (step.evaluate) return cdp.send('Runtime.evaluate', { expression: step.evaluate })
  if (step.startRecording) return recorder.start()
  if (step.stopRecording) return recorder.stop()
  throw new Error(`unknown step: ${JSON.stringify(step)}`)
}

// --- recording --------------------------------------------------------------

function makeRecorder(cdp) {
  const frames = [] // { file, timestamp }
  const writes = []
  let recording = false
  cdp.on('Page.screencastFrame', (params) => {
    // Ack first — an unacked frame stalls the whole screencast.
    void cdp.send('Page.screencastFrameAck', { sessionId: params.sessionId })
    if (!recording) return
    const file = `${OUT_DIR}/frames/frame_${String(frames.length).padStart(5, '0')}.png`
    frames.push({ file, timestamp: params.metadata.timestamp })
    writes.push(Bun.write(file, Buffer.from(params.data, 'base64')))
  })
  return {
    frames,
    // The screencast must be STARTED before the navigation: after a real
    // cross-origin navigation swaps the renderer process, startScreencast
    // fails with "Not attached to an active page". So the CDP stream runs
    // from the beginning, and `recording` decides which frames are kept —
    // the scenario's start/stop markers gate the flag, not the stream.
    async attach() {
      await cdp.send('Page.bringToFront')
      await cdp.send('Page.startScreencast', {
        format: 'png',
        quality: 90,
        maxWidth: WIDTH,
        maxHeight: HEIGHT,
        everyNthFrame: 1,
      })
    },
    start() {
      recording = true
    },
    stop() {
      recording = false
    },
    async finalize() {
      recording = false
      await cdp.send('Page.stopScreencast')
      await sleep(300) // let in-flight frames land
      await Promise.all(writes)
    },
  }
}

/** Concat-demuxer input: each frame shown for the real gap to the next one. */
function concatManifest(frames) {
  const lines = []
  for (let i = 0; i < frames.length; i++) {
    const dur =
      i + 1 < frames.length
        ? Math.max(0.01, frames[i + 1].timestamp - frames[i].timestamp)
        : 0.5
    lines.push(`file '${frames[i].file}'`, `duration ${dur.toFixed(4)}`)
  }
  // concat quirk: the last file must be named once more or it is dropped.
  if (frames.length) lines.push(`file '${frames[frames.length - 1].file}'`)
  return lines.join('\n') + '\n'
}

async function assemble(frames) {
  const manifest = `${OUT_DIR}/concat.txt`
  await Bun.write(manifest, concatManifest(frames))
  const out = `${OUT_DIR}/${scenario.name}.mp4`
  if (!Bun.which('ffmpeg')) {
    console.log(`~ ffmpeg not found; frames kept in ${OUT_DIR}/frames`)
    console.log(`  assemble with: ffmpeg -f concat -safe 0 -i ${manifest} -vf format=yuv420p -movflags +faststart ${out}`)
    return null
  }
  const proc = Bun.spawn(
    ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', manifest,
      '-vf', 'format=yuv420p', '-movflags', '+faststart', out],
    { stdout: 'ignore', stderr: 'ignore' },
  )
  if ((await proc.exited) !== 0) throw new Error('ffmpeg failed to assemble the recording')
  console.log(`→ ${out}`)
  console.log(`  gif: ffmpeg -i ${out} -vf "fps=15,scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse" ${OUT_DIR}/${scenario.name}.gif`)
  return out
}

// --- main -------------------------------------------------------------------

const proc = launchChromium()
let failed = false

try {
  const target = await cdpTarget()
  const cdp = await connect(target.webSocketDebuggerUrl)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  // Fixed metrics so a recording is comparable across machines and displays.
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false,
  })

  const recorder = makeRecorder(cdp)
  await recorder.attach()
  console.log(`→ ${scenario.name}: loading ${scenario.url ?? URL_ARG}`)
  await cdp.send('Page.navigate', { url: scenario.url ?? URL_ARG })

  // Unless the scenario places the marker itself, record from the first step —
  // the loading state is part of the honest demo.
  const managed = scenario.steps.some((s) => s.startRecording)
  if (!managed) recorder.start()

  for (const step of scenario.steps) {
    await runStep(cdp, step, recorder)
  }
  await recorder.finalize()

  if (!recorder.frames.length) throw new Error('no frames were captured')
  console.log(`→ ${recorder.frames.length} frames over ${(
    recorder.frames[recorder.frames.length - 1].timestamp - recorder.frames[0].timestamp
  ).toFixed(1)} s`)
  await assemble(recorder.frames)
} catch (err) {
  console.error('✗ recording failed:', err.message)
  failed = true
} finally {
  proc.kill()
}

process.exit(failed ? 1 : 0)
