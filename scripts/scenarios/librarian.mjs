/**
 * The librarian answers, and the map moves.
 *
 * Opens the panel, asks a real question, and holds while the local model
 * plans, searches, and streams a cited answer — the camera flies to the
 * evidence, cited papers pulse amber, and the trail walks them in order.
 * Budget ~75 s: planning and the answer both run on the local 8B model.
 */
export default {
  name: 'librarian',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 1500 },
    {
      evaluate:
        "[...document.querySelectorAll('.view-toggle button')]" +
        ".find((b) => b.textContent.includes('Librarian')).click()",
    },
    { wait: '.librarian-panel', timeout: 10_000 },
    { click: '.librarian-panel input' },
    { type: 'What does my library say about bioelectric signaling in development?' },
    { sleep: 400 },
    { click: '.librarian-panel form button' },
    { sleep: 70_000 }, // plan → search → directives → streamed answer
    { sleep: 4_000 }, // hold the finished answer, trail crawling
  ],
}
