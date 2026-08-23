precision highp float;

uniform vec3  uFogColor;
uniform float uFogNear;
uniform float uFogFar;
// Per-node contribution. Low, because the picture is built by accumulation:
// with ~30 overlapping sprites in a cluster core, anything near 1.0 saturates.
uniform float uIntensity;
// Radius of the solid centre, as a fraction of the sprite. Everything outside
// it is halo.
uniform float uCoreRadius;

varying vec3  vColor;
varying float vAlpha;
varying float vFogDepth;
varying float vSelected;

void main() {
  // gl_PointCoord is 0..1 across the sprite quad. Work in radius rather than
  // squared distance so the core boundary is a real proportion of the sprite.
  vec2  uv = gl_PointCoord - 0.5;
  float r  = length(uv) * 2.0;      // 0 at centre, 1 at the sprite edge
  if (r > 1.0) discard;

  // --- core: a solid disc, edge softened by barely more than a pixel -------
  // A single smooth falloff makes every node a faint smudge; under additive
  // blending the dense regions then wash out and the sparse ones disappear.
  // Giving each node a definite centre is what makes an individual paper
  // findable inside a cluster.
  float edge = fwidth(r) * 1.5;     // antialias width in this pixel's terms
  float core = 1.0 - smoothstep(uCoreRadius - edge, uCoreRadius + edge, r);

  // --- aura: a gaussian glow beyond the core ------------------------------
  // Gaussian rather than polynomial: it decays smoothly to nothing instead of
  // meeting the sprite edge at a visible seam, and it concentrates the light
  // close to the node so the space between papers stays dark. sigma is a third
  // of the aura's width, which puts three standard deviations at the rim.
  float d = max(r - uCoreRadius, 0.0) / max(1.0 - uCoreRadius, 1e-4);
  float sigma = 0.34;
  float aura = exp(-(d * d) / (2.0 * sigma * sigma));

  // The core is opaque and the aura deliberately faint. Weighted the other
  // way — a broad halo carrying most of the alpha — every node reads as a
  // smudge and a cluster as one continuous glow, which is what the first
  // version did. The halo's job is only to show where nodes overlap.
  float weight = core + aura * 0.16 * (1.0 - core);

  // The core is solid in *alpha*, not in colour. Pushing it toward white made
  // every node read as a white dot and destroyed the cluster hue that tells
  // the reader what region they are looking at. The lift is small: at 57 nodes
  // a generous one looked lit, and at 506 the same value turned every dense
  // region into a white blob, because the lift accumulates along the view ray
  // exactly as the alpha does.
  vec3  color  = vColor * (1.0 + core * 0.18);

  // A selected node gets a bright ring just outside its core, which stays
  // legible even where surrounding accumulation is high.
  float ring = smoothstep(uCoreRadius, uCoreRadius + 0.12, r)
             * (1.0 - smoothstep(uCoreRadius + 0.12, uCoreRadius + 0.30, r));
  color += vec3(1.0) * ring * vSelected * 1.6;

  // Additive has no fog colour to mix toward — adding grey would brighten
  // distant nodes rather than recede them — so depth fades alpha instead.
  float fog = smoothstep(uFogNear, uFogFar, vFogDepth);

  gl_FragColor = vec4(color, weight * vAlpha * uIntensity * (1.0 - fog * 0.8));
}
