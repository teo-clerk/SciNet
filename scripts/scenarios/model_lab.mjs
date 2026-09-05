/**
 * The Model Lab: hardware, catalog, verdicts, routing.
 *
 * Navigates to the Models view and holds — the phase's demo beat is the
 * panel itself: the probed card, the VRAM gauge, measured verdicts with the
 * rejected candidates' warnings, and the task-routing table.
 */
export default {
  name: 'model-lab',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 1200 },
    // The Models button is lab furniture and hidden by default; the store
    // reads the preference once, at load, so turning it on means reloading.
    { evaluate: "localStorage.setItem('scinet.lab','1'); location.reload()" },
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 800 },
    {
      evaluate:
        "[...document.querySelectorAll('.view-toggle button')]" +
        ".find((b) => b.textContent.trim() === 'Models').click()",
    },
    { wait: '.model-lab', timeout: 10_000 },
    { sleep: 3000 },
  ],
}
