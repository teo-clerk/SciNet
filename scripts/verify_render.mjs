/**
 * Headless render gate for the 3D map.
 *
 * Drives a real Chromium over CDP: loads the app, captures console errors and
 * WebGL/shader compile failures, drags the camera to force continuous redraws,
 * and reports frame timings. Run against `vite preview` or the dev server.
 *
 *   bun scripts/verify_render.mjs [url] [--headful]
 *
 * Exit code 0 only if the scene drew, no errors surfaced, and the 95th
 * percentile frame stayed inside the 60 fps budget.
 */
const URL_ARG = process.argv[2] ?? 'http://localhost:4173'
const HEADFUL = process.argv.includes('--headful')
const PORT = 9222
// At 60 Hz vsync, frame times quantise to multiples of 16.67 ms: a clean frame
// lands at ~16.7 and a *dropped* one at ~33.3. Anything under 20 ms means no
// frames were dropped; a 16.7 ms threshold would fail on vsync jitter alone.
const BUDGET_MS = 20

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function launchChromium() {
  const args = [
    `--remote-debugging-port=${PORT}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--user-data-dir=/tmp/scinet-verify-profile',
    '--window-size=1600,1000',
    // Force real GPU rasterisation so the numbers mean something.
    '--enable-gpu',
    '--ignore-gpu-blocklist',
    '--enable-unsafe-webgpu',
    'about:blank',
  ]
  if (!HEADFUL) args.unshift('--headless=new')
  return Bun.spawn(['chromium', ...args], { stdout: 'ignore', stderr: 'ignore' })
}

async function cdpTarget() {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, {
        method: 'PUT',
      })
      if (res.ok) return await res.json()
    } catch {
      /* browser not up yet */
    }
    await sleep(250)
  }
  throw new Error('chromium devtools endpoint never came up')
}

class Cdp {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.events = []
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id !== undefined) {
        const p = this.pending.get(msg.id)
        if (p) {
          this.pending.delete(msg.id)
          msg.error ? p.reject(new Error(JSON.stringify(msg.error))) : p.resolve(msg.result)
        }
      } else {
        this.events.push(msg)
      }
    })
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
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true })
    ws.addEventListener('error', reject, { once: true })
  })
  return new Cdp(ws)
}

/** Drag across the viewport so OrbitControls redraws every frame. */
async function orbit(cdp, steps = 40) {
  const y = 600
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mousePressed', x: 500, y, button: 'left', clickCount: 1,
  })
  for (let i = 0; i < steps; i++) {
    await cdp.send('Input.dispatchMouseEvent', {
      type: 'mouseMoved', x: 500 + i * 14, y, button: 'left',
    })
    await sleep(16)
  }
  await cdp.send('Input.dispatchMouseEvent', {
    type: 'mouseReleased', x: 500 + steps * 14, y, button: 'left', clickCount: 1,
  })
}

const proc = launchChromium()
let failed = false

try {
  const target = await cdpTarget()
  const cdp = await connect(target.webSocketDebuggerUrl)

  await cdp.send('Runtime.enable')
  await cdp.send('Log.enable')
  await cdp.send('Page.enable')

  console.log(`→ loading ${URL_ARG}`)
  await cdp.send('Page.navigate', { url: URL_ARG })

  // The app fetches /api/graph before it can draw anything, so poll for the
  // canvas rather than assuming a fixed delay is enough.
  let appeared = false
  for (let i = 0; i < 40; i++) {
    const probe = await cdp.send('Runtime.evaluate', {
      expression: `!!document.querySelector('canvas')`,
      returnByValue: true,
    })
    if (probe.result.value) { appeared = true; break }
    await sleep(500)
  }
  if (!appeared) {
    const why = await cdp.send('Runtime.evaluate', {
      expression: `document.querySelector('.placeholder')?.innerText ?? '(no placeholder)'`,
      returnByValue: true,
    })
    console.error(`✗ no canvas after 20s. Page says: ${why.result.value}`)
    failed = true
  }
  await sleep(2500)

  // Did a canvas with a live WebGL context actually appear?
  const probe = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const c = document.querySelector('canvas')
      if (!c) return { canvas: false }
      const gl = c.getContext('webgl2') || c.getContext('webgl')
      return {
        canvas: true,
        width: c.width,
        height: c.height,
        contextLost: gl ? gl.isContextLost() : null,
        renderer: gl ? (() => {
          const e = gl.getExtension('WEBGL_debug_renderer_info')
          return e ? gl.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'unknown'
        })() : null,
      }
    })()`,
    returnByValue: true,
  })
  const info = probe.result.value
  console.log('→ canvas:', JSON.stringify(info))

  const drawn = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const el = [...document.querySelectorAll('.status-bar span')]
        .map((s) => s.textContent).join(' | ')
      return el
    })()`,
    returnByValue: true,
  })
  console.log('→ status bar:', drawn.result.value)
  if (!info.canvas || info.contextLost) {
    console.error('✗ no live WebGL canvas')
    failed = true
  }

  // Frame sampler, then a drag to force continuous redraw.
  await cdp.send('Runtime.evaluate', {
    expression: `window.__frames = []; (function s(){
      const t = performance.now();
      if (window.__last) window.__frames.push(t - window.__last);
      window.__last = t;
      requestAnimationFrame(s);
    })();`,
  })
  await orbit(cdp)
  await sleep(500)

  const stats = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const f = window.__frames.slice(10).sort((a, b) => a - b)
      if (!f.length) return null
      const at = (p) => f[Math.min(f.length - 1, Math.floor(f.length * p))]
      return {
        samples: f.length,
        median: +at(0.5).toFixed(2),
        p95: +at(0.95).toFixed(2),
        worst: +f[f.length - 1].toFixed(2),
        fps: Math.round(1000 / at(0.5)),
      }
    })()`,
    returnByValue: true,
  })
  const s = stats.result.value
  console.log('→ frames:', JSON.stringify(s))

  // Anything the page complained about — shader compile failures land here.
  // Page faults are failures; network faults are not. This gate is about the
  // renderer, and it must stay usable with the API deliberately not running.
  const isNetwork = (e) =>
    e.method === 'Log.entryAdded' && e.params.entry.source === 'network'

  const errors = cdp.events.filter(
    (e) =>
      e.method === 'Runtime.exceptionThrown' ||
      (e.method === 'Runtime.consoleAPICalled' && e.params.type === 'error') ||
      (e.method === 'Log.entryAdded' &&
        e.params.entry.level === 'error' &&
        !isNetwork(e)),
  )
  const netWarnings = cdp.events.filter(
    (e) => isNetwork(e) && e.params.entry.level === 'error',
  )

  for (const w of netWarnings) {
    console.log(`  ~ network: ${w.params.entry.text} (${w.params.entry.url ?? ''})`)
  }
  if (errors.length) {
    console.error(`✗ ${errors.length} page error(s) — shader or JS fault:`)
    for (const e of errors.slice(0, 10)) {
      console.error('   ', JSON.stringify(e.params).slice(0, 400))
    }
    failed = true
  } else {
    console.log('→ page errors: none')
  }

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  await Bun.write('/tmp/scinet-render.png', Buffer.from(shot.data, 'base64'))
  console.log('→ screenshot: /tmp/scinet-render.png')

  if (s && s.p95 > BUDGET_MS) {
    console.error(`✗ p95 frame ${s.p95}ms exceeds the ${BUDGET_MS}ms 60fps budget`)
    failed = true
  } else if (s) {
    console.log(`✓ p95 frame ${s.p95}ms within the ${BUDGET_MS}ms budget`)
  }
} catch (err) {
  console.error('✗ verification failed:', err.message)
  failed = true
} finally {
  proc.kill()
}

process.exit(failed ? 1 : 0)
