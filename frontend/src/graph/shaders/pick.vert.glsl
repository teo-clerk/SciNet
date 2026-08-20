// Geometry must match point.vert exactly, or the cursor picks a node that is
// not the one under it.
attribute float aSize;
attribute float aFiltered;
attribute float aSelected;
attribute vec3  aPickColor;   // node index encoded as RGB

uniform float uPixelRatio;
uniform float uBaseSize;

varying vec3  vPickColor;
varying float vFiltered;

void main() {
  vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mvPosition;

  float sizeScale = mix(0.5, 1.0, aFiltered) * (1.0 + 1.4 * aSelected);
  gl_PointSize = uBaseSize * aSize * sizeScale * uPixelRatio * (300.0 / -mvPosition.z);

  vPickColor = aPickColor;
  vFiltered  = aFiltered;
}
