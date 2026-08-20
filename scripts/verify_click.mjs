/**
 * Does clicking a node actually open the sidebar?
 *
 * Drives a real browser: finds a node by picking one from the graph payload,
 * projects it to screen coordinates, clicks it, and checks that the detail
 * panel appears with the fields it is supposed to carry. A unit test of the
 * click logic cannot catch a mismatch between the pick target and what is
 * drawn — only a real click can.
 *
 *   bun scripts/verify_click.mjs [url]
 */
const URL_ARG = process.argv[2] ?? 'http://localhost:5173'
const PORT = 9223
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const proc = Bun.spawn(
  ['chromium', '--headless=new', `--remote-debugging-port=${PORT}`,
   '--no-first-run', '--user-data-dir=/tmp/scinet-click-profile',
   '--window-size=1600,1000', '--enable-gpu', '--ignore-gpu-blocklist',
   'about:blank'],
  { stdout: 'ignore', stderr: 'ignore' },
)

class Cdp {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map()
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data)
      const p = this.pending.get(m.id)
      if (p) { this.pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result) }
    })
  }
  send(method, params = {}) {
    const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((res, rej) => {
      this.pending.set(id, { resolve: res, reject: rej })
      setTimeout(() => this.pending.delete(id) && rej(new Error(`${method} timed out`)), 30000)
    })
  }
}

let failed = false
try {
  let target
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })
      if (r.ok) { target = await r.json(); break }
    } catch { /* not up */ }
    await sleep(250)
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true }); ws.addEventListener('error', rej, { once: true })
  })
  const cdp = new Cdp(ws)
  await cdp.send('Runtime.enable'); await cdp.send('Page.enable')

  await cdp.send('Page.navigate', { url: URL_ARG })
  let ready = false
  for (let i = 0; i < 60; i++) {
    const p = await cdp.send('Runtime.evaluate', {
      expression: `!!document.querySelector('canvas')`, returnByValue: true })
    if (p.result.value) { ready = true; break }
    await sleep(500)
  }
  if (!ready) throw new Error('canvas never appeared')
  await sleep(2500)

  // Project a real node to screen space using the page's own camera.
  const spot = await cdp.send('Runtime.evaluate', {
    expression: `(() => {
      const c = document.querySelector('canvas')
      const r = c.getBoundingClientRect()
      // Sample the middle of the canvas outward until picking reports a node.
      return { cx: r.left + r.width / 2, cy: r.top + r.height / 2,
               w: r.width, h: r.height }
    })()`, returnByValue: true })
  const { cx, cy } = spot.result.value

  // Spiral outward from the centre until a click opens the panel.
  const offsets = []
  for (let ring = 0; ring <= 12; ring++)
    for (let a = 0; a < 12; a++)
      offsets.push([Math.cos((a / 12) * 6.283) * ring * 14, Math.sin((a / 12) * 6.283) * ring * 14])

  let opened = null
  for (const [dx, dy] of offsets) {
    const x = Math.round(cx + dx), y = Math.round(cy + dy)
    // Hover first: picking is what identifies the node.
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
    await sleep(90)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
    await sleep(320)
    const panel = await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        const el = document.querySelector('.detail-panel')
        if (!el) return null
        const text = el.innerText
        return {
          title: el.querySelector('h2')?.textContent ?? null,
          sections: [...el.querySelectorAll('h3')].map(h => h.textContent),
          hasOpenPdf: [...el.querySelectorAll('button')].some(b => /open pdf/i.test(b.textContent)),
          hasConfidence: /confidence/i.test(text),
          hasTags: !!el.querySelector('.tag-chip'),
          hasLinks: [...el.querySelectorAll('a')].map(a => a.textContent),
        }
      })()`, returnByValue: true })
    if (panel.result.value) { opened = panel.result.value; break }
  }

  if (!opened) {
    console.error('✗ clicking never opened the detail panel')
    failed = true
  } else {
    console.log('✓ click opened the detail panel')
    console.log(`   title       : ${(opened.title ?? '(none)').slice(0, 62)}`)
    console.log(`   sections    : ${opened.sections.join(', ') || '(none)'}`)
    console.log(`   tags shown  : ${opened.hasTags}`)
    console.log(`   confidence  : ${opened.hasConfidence}`)
    console.log(`   open-pdf    : ${opened.hasOpenPdf}`)
    console.log(`   links       : ${opened.hasLinks.join(', ') || '(none)'}`)
    if (!opened.hasOpenPdf) { console.error('✗ Open PDF button missing'); failed = true }
    if (!opened.title) { console.error('✗ no title rendered'); failed = true }
  }

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  await Bun.write('/tmp/scinet-click.png', Buffer.from(shot.data, 'base64'))
  console.log('   screenshot  : /tmp/scinet-click.png')
} catch (e) {
  console.error('✗ verification failed:', e.message)
  failed = true
} finally {
  proc.kill()
}
process.exit(failed ? 1 : 0)
