precision highp float;

varying vec3  vPickColor;
varying float vFiltered;

void main() {
  vec2  uv = gl_PointCoord - 0.5;
  if (dot(uv, uv) > 0.25) discard;
  // Filtered-out nodes are unpickable: they are context, not targets.
  if (vFiltered < 0.5) discard;

  // RGB is the node index; alpha is how far away it is.
  //
  // Depth testing already resolves overlaps *per pixel*, but the reader's
  // cursor covers many pixels, and without this the search across them could
  // only rank by distance from the cursor — so a node behind another could win
  // by being a pixel closer to the centre. gl_FragCoord.z is monotonic in
  // distance, which is all a tie-break needs, and the alpha channel was
  // otherwise carrying nothing.
  gl_FragColor = vec4(vPickColor, gl_FragCoord.z);
}
