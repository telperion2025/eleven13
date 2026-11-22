"""
Disk/tape image containers (spec §16): container.py reads/writes the
SIMH-compatible formats losslessly (no emulator-private conversion — "if
SIMH can read it, this emulator can too", §16); simh_compat.py carries
utilitarian importers/exporters for other legacy formats, marked outside
the v1 core scope.

M3/M8 stubs per §19.10.
"""
