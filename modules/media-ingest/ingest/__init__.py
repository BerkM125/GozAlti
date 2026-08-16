"""GozAlti media-ingest — camera graph, feeds, orientation, detection sweep.

Library surface for other modules (harness, vlm, synthesis):

    from ingest.graph import CameraGraph
    g = CameraGraph.load()
    g.nearby(47.6097, -122.3331, radius_m=100)   # lat/lon -> camera nodes
    g.convergence(47.6097, -122.3331, 150)       # SPEC §6.7 shape
    g.street("Pike Street")                      # ordered cameras on a street

    from ingest import feeds
    feeds.latest_frame(node)                     # -> (jpeg bytes, FrameRecord §6.1)

REST surface on :8030 — see ingest/service.py.
"""
