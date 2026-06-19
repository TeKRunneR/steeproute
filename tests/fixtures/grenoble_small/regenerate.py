# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportMissingTypeArgument=false
# pyright: reportPrivateUsage=false
# Reason: same osmnx/networkx boundary as pipeline/osm.py; the regen script reuses
# `pipeline.osm._OSM_CUSTOM_FILTER` so the fixture matches the real download filter.
"""Regenerate the committed osm_graph.graphml fixture for tests/fixtures/grenoble_small/.

Run from this directory:

    python regenerate.py

Verifies TLS via the OS trust store (`truststore` package) so it Just Works
behind corporate TLS-intercepting proxies whose root CA is installed in the
operating-system store but not in `certifi`'s vendored bundle. No `--insecure`
escape hatch is offered: if your environment can't validate Overpass's
certificate via the OS store, fix the trust chain — don't paper over it.
"""

from __future__ import annotations

import pathlib

import osmnx
import truststore

from steeproute.pipeline.osm import _OSM_CUSTOM_FILTER

# Center: Le Sappey-en-Chartreuse, a hiking village in the Chartreuse Massif
# north of Grenoble. Picked for sac_scale variety inside a small radius: T1
# (hiking) through T5 (demanding_alpine_hiking) all represented, including
# osmnx-merged list-valued sac_scale edges that filter_trails has to cope with.
CENTER_LAT = 45.260
CENTER_LON = 5.788
DIST_M = 2000

# Single-source the fetch filter from production so the fixture can never drift
# from what `osm_load` actually fetches (Story 6.2: now trails + minor-road
# connectors). filter_trails() narrows further at query time.
CUSTOM_FILTER = _OSM_CUSTOM_FILTER

OUTPUT_PATH = pathlib.Path(__file__).parent / "osm_graph.graphml"


def main() -> None:
    truststore.inject_into_ssl()

    # osmnx's default useful_tags_way drops sac_scale; we need it.
    osmnx.settings.useful_tags_way = list(osmnx.settings.useful_tags_way) + ["sac_scale"]

    graph = osmnx.graph_from_point(
        center_point=(CENTER_LAT, CENTER_LON),
        dist=DIST_M,
        dist_type="bbox",
        custom_filter=CUSTOM_FILTER,
        retain_all=False,
        simplify=True,
    )
    osmnx.save_graphml(graph, OUTPUT_PATH)
    size_kb = OUTPUT_PATH.stat().st_size / 1024
    print(
        f"Saved {OUTPUT_PATH.name}: {size_kb:.1f} KB, "
        f"{graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"
    )


if __name__ == "__main__":
    main()
