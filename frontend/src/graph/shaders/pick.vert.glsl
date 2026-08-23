// Geometry matches point.vert, then deliberately does not.
//
// The pick sprite is drawn *larger* than the visible one. That is the whole
// trick: this is the GPU-picking equivalent of raising a raycaster's Points
// threshold, and it works the same way — the target grows while what the
// reader sees does not move a pixel. Matching the two exactly is the obvious
// thing to do and it is what made the map miserable to click, because a node
// eighty units away is three pixels across and nobody aims at three pixels.
attribute float aSize;
attribute float aFiltered;
attribute float aSelected;
attribute vec3  aPickColor;   // node index encoded as RGB

uniform float uPixelRatio;
uniform float uBaseSize;
//: How much larger the pick disc is than the drawn one.
uniform float uInflate;
//: Floor, in device pixels. Perspective shrinks a distant node toward nothing;
//: this is what keeps the far side of the map clickable at the same effort as
//: the near side, rather than requiring the reader to fly in first.
uniform float uMinPickSize;

varying vec3  vPickColor;
varying float vFiltered;

void main() {
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;

  float sizeScale = mix(0.5, 1.0, aFiltered) * (1.0 + 1.4 * aSelected);
  float drawn = uBaseSize * aSize * sizeScale * uPixelRatio * (300.0 / -mvPosition.z);
  gl_PointSize = max(drawn * uInflate, uMinPickSize * uPixelRatio);

  vPickColor = aPickColor;
  vFiltered  = aFiltered;
}
