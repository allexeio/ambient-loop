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
| A-007 | Client project inputs are git-ignored by default | Confidentiality | None on results | Proposed — awaiting confirmation |
