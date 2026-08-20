// Per-node visual state lives in attributes, never in React state. Filtering
// and selection are attribute writes, so nothing reallocates and nothing
// re-renders the React tree.
attribute vec3  aColor;
attribute float aSize;
attribute float aFiltered;   // 0 = filtered out (dimmed), 1 = visible
attribute float aSelected;   // 0 | 1

uniform float uPixelRatio;
uniform float uBaseSize;

varying vec3  vColor;
varying float vAlpha;
varying float vFogDepth;

void main() {
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;

  float dim       = mix(0.10, 1.0, aFiltered);
  float sizeScale = mix(0.45, 1.0, aFiltered) * mix(1.0, 2.4, aSelected);

  // Perspective size attenuation: -mvPosition.z is view-space depth.
  vFogDepth   = -mvPosition.z;
  gl_PointSize = uBaseSize * aSize * sizeScale * uPixelRatio * (300.0 / vFogDepth);

  vColor = mix(aColor, vec3(1.0), aSelected * 0.55);
  vAlpha = dim;
}
