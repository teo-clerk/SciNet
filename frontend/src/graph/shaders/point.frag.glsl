precision highp float;

uniform vec3  uFogColor;
uniform float uFogNear;
uniform float uFogFar;
// Per-node contribution. Low, because the picture is built by accumulation:
// with ~30 overlapping sprites in a cluster core, anything near 1.0 saturates.
uniform float uIntensity;

varying vec3  vColor;
varying float vAlpha;
varying float vFogDepth;
varying float vSelected;

void main() {
  // gl_PointCoord is 0..1 across the sprite quad; build a soft disc from it.
  vec2  uv = gl_PointCoord - 0.5;
  float d2 = dot(uv, uv);            // 0.25 at the sprite edge
  if (d2 > 0.25) discard;

  // Tight core, soft halo. The falloff is squared so the sprite has a definite
  // centre rather than reading as an even blob — under additive blending an
  // even blob is what turns a dense cluster into a white smear.
  float disc = smoothstep(0.25, 0.0, d2);
  float alpha = disc * disc;

  // No brightness boost here. Additive blending already brightens wherever
  // sprites overlap, and pre-brightening each one compounds it: an earlier
  // version multiplied by 1.5 and added 0.12, which drove every dense cluster
  // core to pure white and destroyed the colour that identifies it. The colour
  // is emitted at full saturation and allowed to accumulate on its own.
  vec3 color = vColor;

  // A selected node gets a bright ring, which stays legible even where the
  // surrounding accumulation is high.
  float ring = smoothstep(0.15, 0.19, d2) * smoothstep(0.25, 0.20, d2);
  color += vec3(1.0) * ring * vSelected * 1.5;

  // Additive has no fog colour to mix toward — adding grey would brighten
  // distant nodes rather than recede them — so depth fades alpha instead.
  float fog = smoothstep(uFogNear, uFogFar, vFogDepth);

  gl_FragColor = vec4(color, alpha * vAlpha * uIntensity * (1.0 - fog * 0.8));
}
