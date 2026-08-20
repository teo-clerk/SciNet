precision highp float;

varying vec3  vPickColor;
varying float vFiltered;

void main() {
  vec2  uv = gl_PointCoord - 0.5;
  if (dot(uv, uv) > 0.25) discard;
  // Filtered-out nodes are unpickable: they are context, not targets.
  if (vFiltered < 0.5) discard;
  gl_FragColor = vec4(vPickColor, 1.0);
}
