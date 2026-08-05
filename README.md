# satmatch — which Starlink satellite is my dish paired to?

The dish never reports its serving satellite, but its obstruction map records
which sky directions the phased array actually used. At each 15 s scheduling
slot boundary (:12/:27/:42/:57 UTC) this tool resets the map, samples it at
2 Hz, converts the newly-lit pixels to az/el, and matches the resulting sky
track against SGP4 propagation of the full CelesTrak Starlink catalogue.

Validated live on a Starlink Mini: per-slot IDs at 0.3–1.3° mean angular
error with the runner-up 6–8° away (unambiguous). Method after
arXiv:2601.13790 and Ahangarpour et al. (LEO-NET'24), with one significant
correction: the FRAME_UT obstruction map is an azimuthal-equidistant
projection about the **boresight axis in dish body coordinates** — image
roll comes from `ned2dish_quaternion` (image-down = body −y). The published
tilt-shift conversion is 6–10° wrong off-axis. See
`../notes/reference/obstruction-map-geometry.md` for the derivation and
`analyze_geometry.py` for the model-selection analysis.

## Usage

Uses the sibling `../starlink-grpc-tools` clone and `../venv`
(`grpcio yagrc protobuf skyfield numpy`):

```sh
../venv/bin/python satmatch.py identify              # observe until confident
../venv/bin/python satmatch.py identify --watch      # continuous
../venv/bin/python satmatch.py fov                   # read-only: who's near boresight
../venv/bin/python satmatch.py locate                # recover dish position from tracks
../venv/bin/python satmatch.py tle --refresh         # refresh catalogue cache
../venv/bin/python satmatch.py identify --satellite-info   # + launch/age/orbit/status
../venv/bin/python satmatch.py identify --dwells           # handover log: one block per dwell
../venv/bin/python satmatch.py info STARLINK-5539 55297    # lookup by name or NORAD
```

`--dwells` (implies `--watch`) prints only serving-satellite changes: a
start timestamp when a new satellite is confidently identified, and an end
timestamp with dwell duration, data moved (with average rates) and the
persistent all-time tally for that satellite when it changes. Combine with
`--satellite-info` to also get the full info block per new satellite. Slots
without a confident ID neither open nor close a dwell; the close line's
"confirmed in N/M slots" exposes any gaps.

`--satellite-info` / `info` pull launch date+site, age, operational status
and international designator from the CelesTrak SATCAT (cached 7 days) and
the current orbit from the TLE itself. Starlink hardware version is not in
any public catalogue, so it is inferred from launch date + orbital shell and
labelled as a heuristic.

Observer location: `--location lat,lon[,alt_m]` / dish GPS (if the app's
"allow access on local network" is enabled) / `location.json` (written by
`--save-location`, or by `locate`, which recovers the position from the
tracks themselves to ~10 km when the dish GPS is policy-blocked).

**Caveat:** `identify` and `locate` reset the dish's obstruction map every
slot, discarding its learned obstruction history (re-learned in ~12 h).
Don't leave `--watch` running on a link you depend on.

## Tests

```sh
../venv/bin/python test_satmatch.py
```

Includes a synthetic end-to-end (render a real satellite's track into fake
maps, recover it from the full catalogue), propagation validated against
skyfield to <0.25°, and location recovery.
