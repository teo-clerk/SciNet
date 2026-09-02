// Per-node visual state lives in attributes, never in React state. Filtering,
// hover and selection are attribute writes, so nothing reallocates and nothing
// re-renders the React tree.
attribute vec3  aColor;
attribute float aSize;
attribute float aFiltered;   // 0 = filtered out (dimmed), 1 = visible
attribute float aSelected;   // 0 | 0.6 hovered | 1 selected
attribute float aHighlight;  // 1 = the librarian is pointing here

uniform float uPixelRatio;
uniform float uBaseSize;

varying vec3  vColor;
varying float vAlpha;
varying float vFogDepth;
varying float vSelected;
varying float vHighlight;

void main() {
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;

  // Filtered nodes stay on screen but recede: removing them would destroy the
  // spatial context that makes the survivors meaningful.
  float dim       = mix(0.08, 1.0, aFiltered);
  float sizeScale = mix(0.5, 1.0, aFiltered) * (1.0 + 1.4 * aSelected);

  vFogDepth    = -mvPosition.z;
  gl_PointSize = uBaseSize * aSize * sizeScale * uPixelRatio * (300.0 / vFogDepth);

  vColor    = mix(aColor, vec3(1.0), aSelected * 0.45);
  vAlpha    = max(dim, aSelected);
  vSelected = aSelected;
  // Deliberately not in gl_PointSize: the pick shader mirrors the size
  // formula, and a highlight that inflated hitboxes would desynchronise it.
  vHighlight = aHighlight;
}
