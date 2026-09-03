/**
 * The minimal scenario: load the map, let it settle, orbit once.
 *
 * Exists to prove the recorder end-to-end and as the template to copy — each
 * feature adds its own scenario file beside this one rather than growing the
 * harness.
 */
export default {
  name: 'smoke',
  steps: [
    { wait: 'canvas', timeout: 20_000 },
    { sleep: 2000 }, // labels fade in; the map settles
    { orbit: { x: 500, y: 450, steps: 70, dx: 9 } },
    { sleep: 1000 },
  ],
}
