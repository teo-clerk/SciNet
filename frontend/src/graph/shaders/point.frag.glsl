precision highp float;

uniform vec3  uFogColor;
uniform float uFogNear;
uniform float uFogFar;

varying vec3  vColor;
varying float vAlpha;
varying float vFogDepth;

void main() {
  // gl_PointCoord is 0..1 across the sprite quad; build a soft disc from it.
  vec2  uv = gl_PointCoord - 0.5;
  float d2 = dot(uv, uv);            // 0.25 at the sprite edge
  if (d2 > 0.25) discard;            // keeps depth writes correct for the disc

  float rim   = smoothstep(0.25, 0.06, d2);   // antialiased edge
  float core  = smoothstep(0.25, 0.00, d2);   // inner glow
  vec3  color = mix(vColor, vColor * 1.5 + 0.12, core * 0.55);

  float fog = smoothstep(uFogNear, uFogFar, vFogDepth);
  color = mix(color, uFogColor, fog);

  gl_FragColor = vec4(color, rim * vAlpha * (1.0 - fog * 0.6));
}
