# morphport

morphport processes Cell Painting morphological profiling data for the Pediatric Cancer Cell Morphology Atlas (PCCMA), preparing it for display on the PCCMA portal.

## Language

### Inputs

**Project**:
An identifier for the dataset a plate belongs to.
Projects may come from the Cell Painting Gallery, where the project is the gallery's dataset ID (e.g., `cpg0016`), or from elsewhere, with any identifier.

**Batch**:
A set of plates from one project that were processed and imaged together.

**Plate**:
A multi-well plate of cells that was imaged; the unit of input to the pipeline.

**Platemap**:
The per-well metadata table (cell type, treatment, and so on) for a plate layout; several plates may share one platemap.

**Sample sheet**:
The input table listing the plates to process, with one row per plate.
Despite the name, each row is a plate, not a biological sample.
Either "sample sheet" or "samplesheet" is acceptable (the nf-core template uses `samplesheet` in files, parameters, and code); prefer two words in prose.

### Experimental design

**Cell type**:
The cells grown in a well, as recorded in a platemap column whose name varies by project (e.g., `cell_line` or `cell_type`).
Currently each cell type is a single cell line, so the two are treated as equivalent.
_Avoid_: cell line (when referring to the platemap value)

**Cell line**:
A named, cultured line of cells (e.g., A673) and its descriptive attributes, such as cancer type, sex, and age at diagnosis.
A platemap's cell type value refers to a cell line.

**Treatment**:
What was applied to a well, as recorded in a platemap column whose name varies by project (e.g., `treatment` or `broad_sample`), including test compounds, DMSO, and no treatment at all.
_Avoid_: compound, drug, condition (when referring to the treatment value in general)

**Compound library**:
A collection of test compounds, with annotations such as mechanism of action and target, from which a screen draws its treatments (e.g., REPO1).
A compound from the library becomes a treatment when it is applied to a well.

**Control**:
A treatment used as a baseline for comparison with other treatments, such as DMSO or no treatment.

### Profiles

**Feature**:
A single morphological measurement, such as the mean DNA intensity in the nucleus; one column of a profile.
Feature names follow CellProfiler's naming (e.g., `Nuclei_Intensity_MeanIntensity_DNA`), and metadata columns (prefixed `Metadata_`) are not features.
_Avoid_: measurement, variable (when referring to a feature column)

**Feature selection**:
Choosing which features to keep for an analysis group, removing those that are uninformative, redundant, or unreliable.

**Profile**:
A vector of morphological feature values describing one cell or one well.
_Avoid_: sample, observation (when referring to a single cell or well's feature values)

**Imaging channel**:
One stain or imaging mode captured for each image (e.g., DNA, ER, RNA, AGP, Mito, or brightfield), as named in CellProfiler feature names.
"Channel" on its own refers to a Nextflow channel, not an imaging channel.
_Avoid_: fluorescence channel, stain (when referring to the channel)

**Channel group**:
The features whose names refer to the same set of imaging channels (e.g., all features measured only in DNA, or all features correlating DNA with ER).
Features that refer to no imaging channel, such as shape features, form their own channel group.

**Aggregate level**:
Whether profiles describe individual cells (`cell`) or whole wells (`well`); per-well profiles are aggregated from the per-cell profiles in each well.
"Per-cell", "per-well", and "well summary" are acceptable descriptions of the two levels.
_Avoid_: aggregation level, level (on its own), data type

### Partitions

**Partition**:
The profiles from one plate at one aggregate level that share one combination of partition-column values.

**Partition columns**:
The platemap columns that any grouping uses to select or divide profiles, such as `Metadata_treatment`.

**Partition key**:
The combination of partition-column values (plus project) that identifies which partitions belong together across plates.
_Avoid_: condition, combo, condition combo

### Analysis groups

**Grouping**:
A named rule in the analysis groups file that defines how profiles are divided into analysis groups (e.g., `moa_grouping`).
"Grouping entry" is an acceptable term when referring to how a grouping is written in the analysis groups file.
_Avoid_: grouping criteria

**Analysis group**:
A set of profiles that are analyzed together, produced by a grouping (e.g., `kinase_inhibitor` from `moa_grouping`).
"Group" is an acceptable shorthand.

**Subgroup**:
The profiles that share one combination of values of a grouping's subgroup columns.
The grouping is applied separately within each subgroup, so every analysis group belongs to one subgroup.

**Group tag**:
The string that names an analysis group and its output files, made from the aggregate level, the grouping name, and the grouping and subgroup column values that identify the group (e.g., `cell__moa_by_cell_line__treatment=kinase_inhibitor__cell_type=lineA`).

### Comparisons and consensus similarity

**Comparison column**:
The platemap column whose values are compared within an analysis group (e.g., `treatment` to compare treatments within one cell type, or `cell_type` to compare cell types within one treatment).
Per-well profiles that share a value are replicates of one test condition.
It is set per grouping, and a grouping without one has no comparisons.
_Avoid_: consensus column, replicate column (a platemap may have a replicate number column)

**Test condition**:
One value of the comparison column combined with the grouping and subgroup column values that identify an analysis group (e.g., treatment `drugA` in the group for cell type `lineA`, or cell type `lineA` in the group for treatment `drugA`).
Comparison reference wells are never part of a test condition: when comparing treatments, DMSO is not a test condition, and when comparing cell types within `drugA`, the DMSO wells of `lineA` are not part of the `lineA` test condition.
_Avoid_: comparison value, compared item, item, condition (on its own)

**Comparison reference**:
The wells that the test conditions of an analysis group are measured against, chosen by their values in a single platemap column (usually a control such as DMSO in the `treatment` column).
A grouping sets it with `reference_values` (and `reference_column`, which defaults to the comparison column), and only together with a comparison column.
_Avoid_: consensus reference, baseline, negative control (when referring to the wells chosen for this purpose)

**Reference normalization**:
Normalizing an analysis group's per-well profiles to its comparison reference wells, separately for each plate, so that each well describes a change from the reference on its own plate.
Within each plate, each value of the columns that define the analysis group (other than the reference columns), such as each cell type, is normalized to its own reference wells.
It is applied on top of plate normalization and only to groups with a comparison reference.
_Avoid_: renormalization, DMSO normalization, centering (when referring to this step)

**Consensus profile**:
A single profile summarizing all the per-well profiles in an analysis group that share one value of the comparison column, pooled across every plate and batch in the group.
Consensus profiles are built only from per-well profiles, never span projects, and never include reference wells.
When the group has a comparison reference, consensus profiles are built from reference-normalized profiles, so they describe a change from the reference; without one, they are built from the plate-normalized profiles as they are.
_Avoid_: signature, level 5 profile, treatment profile

**Consensus similarity**:
The pairwise similarity between the consensus profiles of one analysis group.

**Phenotypic activity**:
Whether the per-well profiles of one test condition are more similar to each other than to the comparison reference wells, scored by mean average precision (mAP) within one analysis group.
Profiles are compared only with others of the same cell type (more generally, the same values of the columns that define the analysis group other than the reference columns), and the score covers all of the test condition's profiles.
A test condition is called as **active** when its mAP is significant after correcting for the number of test conditions in the group.
It is defined only for groups with a comparison reference.
_Avoid_: activity score, hit (when referring to a test condition called as active)
