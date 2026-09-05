/**
 * The AI-tools dialog: open it, walk the four client tabs, copy the line.
 *
 * Gated on selectors, never on a bare sleep: the dialog's recipe comes from
 * GET /api/mcp, so the snippet appears when the API has answered, not after
 * a guessed delay. Headless Chromium may refuse the clipboard, in which case
 * the button stays "Copy" — the recording is of the dialog, not the paste.
 */
const clickTab = (label) =>
  `[...document.querySelectorAll('.mcp-modal [role=tab]')]` +
  `.find((b) => b.textContent.trim() === ${JSON.stringify(label)}).click()`

export default {
  name: 'mcp-modal',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 1500 }, // the map settles behind the dialog
    { click: '.mcp-toggle' },
    { wait: '.mcp-modal pre code', timeout: 10_000 },
    { sleep: 2600 }, // the Claude Code line, long enough to read
    { evaluate: clickTab('Claude Desktop') },
    { sleep: 1800 },
    { evaluate: clickTab('Cursor') },
    { sleep: 1800 },
    { evaluate: clickTab('Zed') },
    { sleep: 1800 },
    { evaluate: clickTab('Claude Code') },
    { sleep: 800 },
    { click: '.mcp-modal .copy' },
    { sleep: 1600 },
  ],
}
