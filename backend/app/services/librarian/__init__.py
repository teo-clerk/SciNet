"""The librarian: a local agent whose answers move the map.

Tools are in-process calls to the machinery that already exists — FTS,
the vector store, markdown on disk — never HTTP hops to our own API. The
answer is streamed prose whose citations are validated in flight against
the evidence the tools actually returned; the map directives (fly,
highlight, trail) are derived from the same evidence, so nothing the
reader sees can outrun what was retrieved.
"""
