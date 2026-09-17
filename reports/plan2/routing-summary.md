# Support routing failures on the three float-valve parts

Measured 2026-09-07 with `scripts/routing_probe.py`, original pose, 5 mm lift,
default settings. The probe reproduces `route_contacts`' own decision sequence
contact by contact and records the quantity that decided each one; its routed
and failed counts match the router exactly in every run below.

## The diagnosis

Roughly half of every part's requested contacts failed to route, on both the
exact (cover) and the raster (nut, body) assembly paths. `gotchas.md` already
recorded that raw interior faces could not explain it. They did not: **the
binding constraint was the router's own tip length.**

`route_contacts` would anchor on already-printed model material only when the
gap between the contact and the material below it exceeded a full
`support.tip_length_mm` (2.0 mm). On these parts the overwhelming majority of
blocked contacts sit 1.39–1.48 mm above the material below them — far enough
to need a support, too close to fit one the router would emit.

The remaining failures are a different class: the contact lands in a column the
coarse analysis grid (0.15 mm) reports as solid at that layer, so there is
neither a vertical route nor any material *below* to anchor on. These are
samples on near-vertical walls, and a two-cell search around them finds no free
column either.

## Measurements

| part | contacts | routed before | routed after | failed before | failed after |
|---|---:|---:|---:|---:|---:|
| nut | 229 | 110 | 195 | 119 | 34 |
| cover | 773 | 389 | 722 | 384 | 51 |
| body | 1,085 | 516 | 994 | 569 | 91 |

Binding constraint before the change:

| part | tip longer than the gap | contact inside material on the analysis grid |
|---|---:|---:|
| nut | 85 | 34 |
| cover | 333 | 51 |
| body | 482 | 87 |

A what-if pass measured each candidate fix alone against the same failed
contacts. Widening the branch search from 8 to 64 candidates recovered **zero**
contacts on all three parts — the nearby columns are genuinely blocked, so
search breadth was not the problem. Attributing a sample to a free column
within two analysis cells recovered 9 on the cover and 7 on the body, and none
on the nut.

## What was changed

`support.min_tip_length_mm`, default 0.30 mm — the contact depth from the
known-good CHITUBOX configuration recorded in `plan2.md` Tier 2.1. When a
contact sits closer to the material below it than one full tip, the tip cone
spans the whole gap and no cylindrical pillar section is emitted. Shorter than
that minimum is still a refusal. `contacts_with_shortened_tip` and
`min_tip_used_mm` are reported per plan.

## The remaining failures rest on material printed below them

Asked again on the printer's own lattice rather than the 0.15 mm analysis grid
(`scripts/routing_probe.py --exact-attachment`), every remaining unroutable
contact is attached:

| part | still unroutable | own pixel occupied one layer below | any pixel of its 3x3 occupied |
|---|---:|---:|---:|
| nut | 34 | 9 | 34 |
| cover | 51 | 37 | 50 |
| body | 91 | 54 | 91 |

A 3x3 neighbourhood at 0.018 mm pitch is a 0.054 mm window, so a sample outside
its own pixel is still within about 0.036 mm of printed material. The samples
sit on near-vertical walls and fall on either side of the surface boundary,
which is why the own-pixel column varies so much between parts while the 3x3
column does not.

Occupied material one layer below is exactly the attachment `raster_connectivity`
certifies, and that check passes on all three parts. So these are not islands.
What is left is a **mechanical** question -- whether a face attached along its
length still sags -- and `downward_contacts` selects purely by face angle, which
cannot tell a free-floating overhang from one attached the whole way down.

**No filter was enabled.** `gotchas.md` already records that dropping samples to
make routing pass is not a fix, and the decision here needs the sag question
answered rather than a threshold chosen to clear a gate.

## What this does not establish

A routed contact is a geometric route, not a proof that the pillar holds or
that it can be removed. Support mechanics remain uncalibrated. After the change
the nut still fails validation on `support_routes` (34), `support_coverage` (4
uncovered samples of 14,520), one single-voxel `enclosed_voids` component
(1.62e-5 mm3, exactly one pixel-layer) and six drainage bottlenecks of about
0.4 mm3 total in support crevices. Tier 0.1 acceptance is not reached.
