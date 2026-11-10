# Reference normalization is a separate per-group step

Plate normalization fits each plate to all of its wells by default, because some projects (such as the cell-line atlas) have no controls, but phenotypic activity and consensus similarity compare profiles to a comparison reference such as DMSO.
We therefore apply reference normalization per analysis group, after feature selection and on top of plate normalization, in its own module (`PYCYTOMINER_REFERENCE_NORMALIZE`): each plate's per-well profiles are normalized (center and scale, with the per-well plate normalization method) to that plate's reference wells, matched on the comparison column when it is not a reference column.
Only groups with a comparison reference are reference normalized; phenotypic activity and consensus similarity both read its output for those groups, so both analyses use the same profiles.

## Considered options

- **Global plate normalization to DMSO** (`--pycytominer_normalize_reference_profiles`): already possible, but it applies to every aggregate level and grouping, fails for projects without controls, and cannot match reference wells to each cell type when a plate holds several.
- **Reference normalization inside the phenotypic activity module**, which would pass profiles through for groups without a reference: fewer tasks, but it couples normalization to one analysis and makes consensus similarity depend on activity for its input.
- **Extending `PYCYTOMINER_NORMALIZE`**: it normalizes one plate file with one query; reference normalization needs a group's files from many plates, fits per plate (and comparison value), filters features and wells, writes a summary, and must not overwrite the plate normalization `meta` keys.
- **Center only** (the earlier consensus similarity spec): rejected to mirror the Way Lab's REPO1 processing, which normalizes each plate to its DMSO wells with `mad_robustize`.

## Consequences

- Median/MAD and mean/SD normalization are affine per feature, so normalizing plate-normalized profiles to the reference gives the same values as normalizing raw profiles to the reference; the plate normalization reference does not matter for these analyses.
  Feature selection, however, still runs on profiles normalized to all wells.
- Features with zero spread in the reference wells are dropped, and wells on plates without reference wells are dropped.
- Consensus similarity no longer centers profiles itself; it reads reference-normalized profiles for groups with a reference and feature-selected profiles otherwise.
