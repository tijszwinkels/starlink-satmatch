# The obstruction-map projection, reverse-engineered

The dish's obstruction map is the only signal that reveals which sky
directions the phased array actually used, so converting its pixels to
az/el correctly is the whole ballgame. This document describes the
projection satmatch uses for `FRAME_UT` maps, why it differs from the
published reference implementations, and the measurements that decided it.

## The map

`dish_get_obstruction_map` returns a square grid of floats (123×123 on
current firmware; response ≈ 60 kB):

- `-1.0` — outside the field of view
- `0.0` — inside the FOV, never used
- `> 0.0` — a direction the beam has actually used (an "SNR" value)

plus `min_elevation_deg` (10), `max_theta_deg` (80), and since the
September 2024 firmware a `map_reference_frame` enum: `FRAME_EARTH` (1) or
`FRAME_UT` (2). Which frame you get depends on the service plan, not the
hardware (arXiv:2601.13790 §III). `dish_clear_obstruction_map` resets the
grid, which is what makes time-resolved beam trails possible: reset at a
scheduling-slot boundary, sample at 2 Hz, and each newly lit pixel is where
the beam pointed during that second.

Pixel conventions throughout: `X` = column (0 = left), `Y` = row (0 = top),
scale `80/62` degrees per pixel from the grid center `(62, 62)`.

## FRAME_EARTH

The straightforward frame: an azimuthal-equidistant projection about the
zenith, north up. Radius from center = zenith angle, `atan2(dx, dy_up)` =
compass azimuth. satmatch implements this per the LEOViz reference
implementation (untested here — our dish reports FRAME_UT; reports welcome).

## FRAME_UT: what the reference implementations assume

LEOViz (clarkzjw/LEOViz) and SatInView (aliahan/SatInView, LEO-NET'24)
treat a FRAME_UT map as the same zenith-centered projection, with the
zenith point shifted up-image by `tilt/(80/62)` pixels and the image
rotated so that image-down points along the boresight azimuth.

That model is exact *along the boresight-zenith great circle* and a good
approximation near the boresight axis. Off-axis it diverges — two
azimuthal-equidistant projections about different centers cannot agree
everywhere — and it silently assumes the image has **zero roll** about the
boresight axis.

On our test dish (a kickstand-mounted Starlink Mini, 26° tilt), tracks near
the boresight matched satellites at ~1°, but tracks 40–55° off-axis missed
by 6–10°, and location fits based on the model came out biased by ~100 km.

## FRAME_UT: the model that fits

The map is an azimuthal-equidistant projection about the **boresight axis,
rendered in the dish's body frame**:

- The center pixel is the boresight (verified: the dish z-axis derived from
  the status quaternion lands on the reported boresight az/el exactly).
- Pixel radius → angle off boresight θ, scale 80/62 °/px.
- Image angle ψ = `atan2(dx, dy)` (0 = image-down, clockwise), rotated by a
  **roll** ρ about the axis. A sky direction is then

  `v = cosθ·b + sinθ·(cos(ψ+ρ)·u1 + sin(ψ+ρ)·u2)`

  where `b` is the boresight unit vector, `u1` the unit vector
  perpendicular to `b` in its vertical plane pointing away from zenith, and
  `u2 = u1 × b`.
- **ρ comes from the `ned2dish_quaternion` in `get_status`: the image-down
  axis is the dish body −y axis.** With `R` the quaternion's rotation
  matrix (columns = body axes in NED), project `−y_body` onto the plane
  perpendicular to `b` and measure its angle from `u1` toward `u2`.

At ρ = 0 this reduces to the tilt-shift model along the boresight vertical
plane, which is why the reference implementations work near-axis and on
dishes that happen to sit roll-free. A kickstand Mini sits however it was
placed — ours had 23° of roll, which the zero-roll assumption turns into
multi-degree pointing error off-axis.

Implementation: `pixel_to_azel_bs` / `roll_from_quaternion` in
`geometry.py`; the legacy tilt-shift model is kept alongside for
comparison.

## How the model was selected

`analyze_geometry.py` re-annotates logged pixel trails under both models
and scores them by the best achievable mean angular separation against
SGP4 propagation of the full catalogue (16 slots of live data, 15 usable
segments):

| model | mean best-ε |
|---|---|
| tilt-shift (reference implementations) | ~10° |
| boresight-AE, roll fitted ρ = 23–24° | **0.79°** |

The empirically fitted roll matched the quaternion prediction for
"image-down = body −y" (23.0°) to within a degree; the other axis
hypotheses (±x, +y) were 90°/180° away. Fit and prediction agreeing from
independent directions is what settled the convention.

With the corrected model, live per-slot identification runs at ε 0.3–1.3°
with the runner-up candidate typically 6–8° away — unambiguous — and the
track-based location fit (see `locate`) converges to ~10–20 km with a 0.9°
residual.

## Practical notes

- Handover slots are 15 s, globally synchronized at :12/:27/:42/:57 UTC.
  The serving satellite often dwells for several consecutive slots, but the
  dish also re-targets *within* a slot when the link degrades (dynamic beam
  switching, confirmed in arXiv:2601.13790) — trails must be segmented on
  pixel jumps or those slots produce confidently wrong matches.
- A serving satellite moves ~1 px/s across the map; the map appears to
  update at ~1 Hz. Sampling at 2 Hz halves timestamp quantization; use the
  midpoint of the sample interval that lit a pixel.
- Boresight az/el in status wanders ±1° between reads (attitude filter
  noise); read it per-slot rather than once.
- The dish's `downlink/uplink_throughput_bps` fields count only space-link
  traffic: hammering the gRPC API at ~12 Mbit/s produces no counter
  response, so polling doesn't contaminate transfer measurements.

## References

- Demystifying Starlink Performance under Vehicular Mobility —
  <https://arxiv.org/abs/2601.13790> (frames, slot grid, beam switching)
- Ahangarpour et al., *Trajectory-based Serving Satellite Identification
  with User Terminal's Field-of-View*, LEO-NET'24 —
  <https://github.com/aliahan/SatInView>
- LEOViz — <https://github.com/clarkzjw/LEOViz>
- starlink-grpc-tools — <https://github.com/sparky8512/starlink-grpc-tools>
