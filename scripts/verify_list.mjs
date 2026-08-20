/** Does the List View render, filter, sort and open the sidebar? */
const URL_ARG = process.argv[2] ?? 'http://localhost:5173'
const PORT = 9224
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const proc = Bun.spawn(['chromium', '--headless=new', `--remote-debugging-port=${PORT}`,
  '--no-first-run', '--user-data-dir=/tmp/scinet-list-profile', '--window-size=1600,1000',
  '--enable-gpu', 'about:blank'], { stdout: 'ignore', stderr: 'ignore' })

class Cdp {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map()
    ws.addEventListener('message', (e) => { const m = JSON.parse(e.data); const p = this.pending.get(m.id)
      if (p) { this.pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result) } }) }
  send(method, params = {}) { const id = ++this.id
    this.ws.send(JSON.stringify({ id, method, params }))
    return new Promise((res, rej) => { this.pending.set(id, { resolve: res, reject: rej })
      setTimeout(() => this.pending.delete(id) && rej(new Error(`${method} timed out`)), 30000) }) }
}
const ev = (cdp, expr) => cdp.send('Runtime.evaluate', { expression: expr, returnByValue: true }).then(r => r.result.value)

let failed = false
try {
  let target
  for (let i = 0; i < 60; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/new?about:blank`, { method: 'PUT' })
      if (r.ok) { target = await r.json(); break } } catch {}
    await sleep(250)
  }
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.addEventListener('open', res, {once:true}); ws.addEventListener('error', rej, {once:true}) })
  const cdp = new Cdp(ws); await cdp.send('Runtime.enable'); await cdp.send('Page.enable')
  await cdp.send('Page.navigate', { url: URL_ARG })
  for (let i = 0; i < 60; i++) { if (await ev(cdp, `!!document.querySelector('canvas')`)) break; await sleep(500) }
  await sleep(2000)

  // Switch to the list.
  await ev(cdp, `[...document.querySelectorAll('.view-toggle button')].find(b=>/list/i.test(b.textContent))?.click()`)
  await sleep(600)

  const shape = await ev(cdp, `(() => {
    const t = document.querySelector('.list-view table'); if (!t) return null
    return { rows: t.querySelectorAll('tbody tr').length,
             headers: [...t.querySelectorAll('thead th')].map(h=>h.textContent.replace(/[▲▼]/g,'').trim()).filter(Boolean),
             tagPills: t.querySelectorAll('.tag-chip').length,
             confidenceBars: t.querySelectorAll('.bar').length,
             actions: [...new Set([...t.querySelectorAll('.actions-cell button')].map(b=>b.textContent))] }
  })()`)
  if (!shape) { console.error('✗ list did not render'); failed = true }
  else {
    console.log(`✓ list rendered: ${shape.rows} rows`)
    console.log(`   columns    : ${shape.headers.join(', ')}`)
    console.log(`   tag pills  : ${shape.tagPills}`)
    console.log(`   confidence : ${shape.confidenceBars} bars`)
    console.log(`   actions    : ${shape.actions.join(', ')}`)
  }

  // Search must filter the list too.
  await ev(cdp, `(() => { const i = document.querySelector('.search-input input')
    const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    set.call(i, 'yawning'); i.dispatchEvent(new Event('input',{bubbles:true})) })()`)
  await sleep(700)
  const filtered = await ev(cdp, `document.querySelectorAll('.list-view tbody tr').length`)
  console.log(`   search 'yawning' -> ${filtered} row(s)`)
  if (filtered === null || filtered >= shape.rows) { console.error('✗ search did not filter the list'); failed = true }

  // Clear, then click a row to open the sidebar.
  await ev(cdp, `(() => { const i = document.querySelector('.search-input input')
    const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set
    set.call(i, ''); i.dispatchEvent(new Event('input',{bubbles:true})) })()`)
  await sleep(500)
  await ev(cdp, `document.querySelector('.list-view tbody tr')?.click()`)
  await sleep(700)
  const panel = await ev(cdp, `(() => { const el = document.querySelector('.detail-panel')
    return el ? { title: el.querySelector('h2')?.textContent ?? null,
                  sections: [...el.querySelectorAll('h3')].map(h=>h.textContent) } : null })()`)
  if (!panel) { console.error('✗ clicking a row did not open the sidebar'); failed = true }
  else console.log(`✓ row click opened the sidebar: ${(panel.title ?? '').slice(0,48)}`)

  // Sorting.
  const before = await ev(cdp, `document.querySelector('.list-view tbody tr .title-cell')?.textContent`)
  await ev(cdp, `[...document.querySelectorAll('.list-view thead th')].find(h=>/year/i.test(h.textContent))?.click()`)
  await sleep(500)
  const after = await ev(cdp, `document.querySelector('.list-view tbody tr .title-cell')?.textContent`)
  console.log(`✓ sort by year changed the first row: ${before !== after}`)
  if (before === after) { console.error('✗ sorting had no effect'); failed = true }

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  await Bun.write('/tmp/scinet-list.png', Buffer.from(shot.data, 'base64'))
  console.log('   screenshot : /tmp/scinet-list.png')
} catch (e) { console.error('✗ failed:', e.message); failed = true }
finally { proc.kill() }
process.exit(failed ? 1 : 0)
