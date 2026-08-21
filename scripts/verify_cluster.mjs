/**
 * Does clicking a cluster label open the inspector, and does picking a paper
 * out of it keep the list open?
 *
 * The cluster labels are troika text inside the WebGL canvas, so there is no
 * DOM node to query or click — the only way to know the handler is wired to
 * what is actually drawn is to click real pixels and see what appears. The
 * script scans the canvas for a label, then checks the two behaviours the
 * inspector exists for: it shows the region's papers, and clicking one opens
 * that paper without closing the region.
 *
 *   bun scripts/verify_cluster.mjs [url]
 */
const URL_ARG = process.argv[2] ?? 'http://localhost:5173'
const PORT = 9224
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const proc = Bun.spawn(
  ['chromium', '--headless=new', `--remote-debugging-port=${PORT}`,
   '--no-first-run', '--user-data-dir=/tmp/scinet-cluster-profile',
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
    } catch { /* not up yet */ }
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

  const box = await cdp.send('Runtime.evaluate', {
    expression: `(() => { const r = document.querySelector('canvas').getBoundingClientRect()
      return { left: r.left, top: r.top, w: r.width, h: r.height } })()`,
    returnByValue: true })
  const { left, top, w, h } = box.result.value

  const readInspector = `(() => {
    const el = document.querySelector('.cluster-inspector')
    if (!el) return null
    return {
      label: el.querySelector('h2')?.textContent ?? null,
      sections: [...el.querySelectorAll('h3')].map(x => x.textContent),
      terms: el.querySelectorAll('.tag-list .chip').length,
      members: el.querySelectorAll('.member-list button').length,
      hasMeta: !!el.querySelector('.member-meta'),
    }
  })()`

  // Sweep the canvas for a label. Cluster names sit above their centroids, so
  // the useful band is the middle of the frame rather than its edges.
  let opened = null
  const STEP = 40
  outer:
  for (let y = top + h * 0.15; y < top + h * 0.85 && !opened; y += STEP) {
    for (let x = left + w * 0.1; x < left + w * 0.9; x += STEP) {
      const px = Math.round(x), py = Math.round(y)
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: px, y: py })
      await sleep(25)
      await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: px, y: py, button: 'left', clickCount: 1 })
      await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: px, y: py, button: 'left', clickCount: 1 })
      await sleep(120)
      const got = await cdp.send('Runtime.evaluate', { expression: readInspector, returnByValue: true })
      if (got.result.value) { opened = got.result.value; break outer }
    }
  }

  if (!opened) {
    console.error('✗ no click anywhere on the canvas opened the cluster inspector')
    failed = true
  } else {
    console.log('✓ clicking a cluster label opened the inspector')
    console.log(`   region      : ${opened.label}`)
    console.log(`   sections    : ${opened.sections.join(', ') || '(none)'}`)
    console.log(`   terms shown : ${opened.terms}`)
    console.log(`   members     : ${opened.members}`)
    if (opened.members === 0) { console.error('✗ member list is empty'); failed = true }
    if (!opened.hasMeta) { console.error('✗ members carry no year/author/confidence row'); failed = true }

    // Picking a paper must open it *without* closing the region it came from.
    const picked = await cdp.send('Runtime.evaluate', {
      expression: `(() => {
        const b = document.querySelector('.cluster-inspector .member-list button')
        if (!b) return null
        b.click()
        return true
      })()`, returnByValue: true })
    if (picked.result.value) {
      await sleep(600)
      const after = await cdp.send('Runtime.evaluate', {
        expression: `(() => ({
          detail: !!document.querySelector('.detail-panel'),
          detailTitle: document.querySelector('.detail-panel h2')?.textContent ?? null,
          inspectorStillOpen: !!document.querySelector('.cluster-inspector'),
        }))()`, returnByValue: true })
      const a = after.result.value
      console.log(`   paper opens : ${a.detail} (${(a.detailTitle ?? '').slice(0, 48)})`)
      console.log(`   list stays  : ${a.inspectorStillOpen}`)
      if (!a.detail) { console.error('✗ clicking a member did not open the detail panel'); failed = true }
      if (!a.inspectorStillOpen) { console.error('✗ opening a paper closed the cluster list'); failed = true }
    }
  }

  const errors = await cdp.send('Runtime.evaluate', {
    expression: `window.__scinetErrors?.length ?? 0`, returnByValue: true })
  if (errors.result.value) { console.error(`✗ ${errors.result.value} page errors`); failed = true }

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  await Bun.write('/tmp/scinet-cluster.png', Buffer.from(shot.data, 'base64'))
  console.log('   screenshot  : /tmp/scinet-cluster.png')
} catch (e) {
  console.error('✗ verification failed:', e.message)
  failed = true
} finally {
  proc.kill()
}
process.exit(failed ? 1 : 0)
