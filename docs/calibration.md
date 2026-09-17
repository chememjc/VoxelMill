# Calibration status

Everything this application computes is geometric. This page lists which numbers
are measured, which are copied from evidence, and which are guesses that need a
real print to settle. **No value below has been calibrated against hardware.**

## What is measured, not calibrated

These are exact or conservatively bounded computations over the actual geometry,
and they do not need calibration to be trusted for what they claim:

- Layer connectivity, islands and overlap on the documented binary masks.
- Bounds, transforms and envelope fit, over every original triangle.
- Repair deviation, verified bidirectionally with a 1-Lipschitz covering bound.
- GOO framing, checksums and decoded pixel equality.

Their limits are resolution limits, stated with each result: analysis pitch,
sampling basis, and whether the grid closed.

## Runtime and memory figures

`scripts/benchmark.py` is where any runtime or peak-RSS figure for this
project comes from. Each scenario runs in its own fresh child process, which
reports its own `RUSAGE_SELF` peak, so the numbers are per-scenario and
comparable with each other — unlike the project's earlier performance
figures, which were measured sequentially in one process and are cumulative
high-water marks. Wall time measured this way is still not a calibrated
number: on a shared machine it is a smoke alarm for a regression, not a
measurement, which is why the script's time tolerance is loose and its
memory tolerance tight.

## Uncalibrated starting points

| Value | Default | Basis |
| --- | --- | --- |
| `spacing_mm` | 3.0 | Requested target. Islands and load may force denser contacts; every override is reported. |
| `contact_diameter_mm` | 0.4 | Medium-support starting dimension. |
| `penetration_mm` | 0.15 | Starting dimension. |
| `pillar_diameter_mm` | 1.2 | Starting dimension. |
| `tip_length_mm` | 2.0 | Starting dimension. |
| `raft_thickness_mm` / `raft_expansion_mm` | 1.0 / 2.0 | Starting dimensions. |
| `max_slenderness` | 40 | Heuristic stiffness limit. Not a buckling calculation. |
| `max_span_mm` | 3.0 | Printability heuristic. |
| `max_contact_gap_mm` | derives `spacing_mm` | Geometric reach only. |
| `max_contact_load_mm2` | derives `4 * spacing_mm^2` | Share of area by nearest assignment. Not a strength result, which is why exceeding it warns rather than fails. |
| `max_deviation_mm` | 0.05 | Requested repair tolerance. |
| `min_orifice_area_mm2` | 1.0 | Requested drainage threshold. |
| Orientation weights | see `geometry.py` | Dimensionless composite. Trapped resin and sealed cavities are weighted above convenience terms, but the ratios are guesses. |

## Process settings

Layer 0.05 mm, bottom/normal exposure 35 / 3.5 s, four bottom layers, five linear
transition layers, rest after exposure 1 s, settle before exposure 0.5 s, wait
after lift 0 s. These are **the user's stated Sunlu ABS-like gray process**, not
measurements taken by this application and not the reference GOO's settings.
Bottom and normal waits are independently configurable and start equal.

## The reference GOO

`right_temporal_bone_..._uvtools-good.goo` is immutable compatibility evidence.
It establishes that a V3.0 file for this machine decodes, and its 18 `motion`
fields make an export serializable. It does **not** establish:

- The units or physical meaning of those motion fields.
- Tilt-release behavior.
- Physical orientation or mirroring on the actual LCD.
- Total print duration.

Its exposures and layer counts differ from the configured profile and must never
replace it. `PrintTime` stays zero unless a caller supplies a measured estimate;
it is not inferred from the reference. Review every motion field manually before
a physical print.

## What a first calibration print should settle

1. **Orientation and mirroring.** Print an asymmetric fixture and confirm the
   part is not mirrored on the LCD. This is the cheapest test with the largest
   consequence.
2. **Exposure.** Confirm the bottom/normal exposures and the transition ramp on
   the actual resin, then record the measured values in the `.res` profile.
3. **Support strength and removability.** The pillar dimensions, slenderness
   limit and anchor-load limit are all guesses until a supported part is printed
   and the supports are removed by hand.
4. **Drainage threshold.** 1 mm² is a requested figure. A print with bores at,
   above and below it shows what actually drains for this resin's viscosity.
5. **Motion and tilt.** Verify each motion field against firmware behavior
   before trusting the copied defaults.
6. **Print duration.** Only after a completed print can `--print-time-s` carry a
   real number.

Until those are done, a passing validation means the geometry is sound, not that
the print will succeed.

## Hardware status

Read-only printer traffic has now been exercised against the idle Mars 5 Ultra:
status, attributes, print history, and the printer-supplied RTSP stream were
retrieved without starting a job, uploading a file, moving the platform, or
changing settings. A live H.264 1280×720 frame decoded successfully and a
history time-lapse downloaded and probed successfully. This establishes network
and protocol connectivity only. Release-film counters remain telemetry, not a
calibrated FEP-health measurement; physical print acceptance, mirroring,
support mechanics, LED uniformity, and timing calibration remain deferred.
Discovery is an explicit, opt-in LAN action and is never triggered by
constructing or connecting an adapter.
