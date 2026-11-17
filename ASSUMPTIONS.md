# Assumptions register

Each entry has an ID, a status and its effect on results. The CLI and UI print the entries that
apply to each run.

| ID | Assumption | Basis | Effect on results | Status |
|---|---|---|---|---|
| A-001 | ΔT_min defaults: liquid–liquid 2 K, air–liquid 5 K, via store 2 × 2 K | Typical plate HX and coil approaches | Changes direct-recovery potential only (§3.1) | Proposed |
| A-002 | Pinch band width 2.5 K for heat-pump-assisted matching | Resolution vs runtime | Small; tested for sensitivity | Proposed |
| A-003 | Greedy minimum-lift matching for HP-assisted potential | Heuristic; LP cross-check on sampled days | Potential is a screening figure, not a design figure | Proposed |
| A-004 | Fixture 1 "overall ratio" = (heat + coolth delivered) / electricity | Arithmetic on brief figures (2,912.4 / 926 = 3.145) | Convention must be labelled in every report | Confirmed by arithmetic |
| A-005 | Fluid properties from Melinder (2010) / ASHRAE Fundamentals ch.31 correlations | Published data; avoids a CoolProp dependency | Expect within ~1–2 % of tabulated values; unit-tested | Proposed |
| A-006 | Default VSD pump and motor efficiency curve | EN 50598 IE classes | Pump energy; replace with selection data when available | Proposed |
| A-007 | Client inputs (`inputs/`, `projects/fixture1/`) are git-ignored; the Fixture 1 regression test skips without them | Confidentiality | None on results | Adopted (provisional answer, not yet confirmed) |
| A-008 | A bare number in a project file is tagged [Assumption] until a source is given | Conservative provenance | Headline tags only | Adopted |
| A-009 | Dry cooler available when dry bulb ≤ cutoff (inclusive) | Matches workbook column AM | Hours at exactly 27.0 °C | Adopted |
| A-010 | Incremental convention: cooling keeps its isolated electricity; the remainder is split between heat services in proportion to their pro-rata electricity | Brief silent on the split between heat services | Per-service heat ratios only; totals unchanged | Adopted, review |
| A-011 | Simple mode: listed pump heat is not returned to the loop | Matches workbook ("excludes pump heat") | Small, only when pumps are switched on | Adopted (simple mode only) |
| A-012 | Isolated reference excludes the dry cooler and has no reuse | Matches workbook columns AA–AC | Isolated ratios only | Adopted |
| A-013 | ASHP COP held at the map end values outside −5 to 10 °C, and those hours are flagged [Extrapolated] | Brief: extrapolation is always flagged | Every Fixture 1 metric carries the [Extrapolated] tag (4,954 h) | Adopted |
