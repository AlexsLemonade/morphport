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
The cells grown in a well, as recorded in the platemap's `cell_type` column.
Currently each cell type is a single cell line, so the two are treated as equivalent.
_Avoid_: cell line (prefer the platemap's term)

**Treatment**:
What was applied to a well, as recorded in the platemap's `treatment` column, including test compounds, DMSO, and no treatment at all.
_Avoid_: compound, drug, condition (when referring to the treatment value in general)

**Control**:
A treatment used as a baseline for comparison with other treatments, such as DMSO or no treatment.

### Profiles

**Profile**:
A vector of morphological feature values describing one cell or one well.
_Avoid_: sample, observation (when referring to a single cell or well's feature values)

**Imaging channel**:
One stain or imaging mode captured for each image (e.g., DNA, ER, RNA, AGP, Mito, or brightfield), as named in CellProfiler feature names.
"Channel" on its own refers to a Nextflow channel, not an imaging channel.
_Avoid_: fluorescence channel, stain (when referring to the channel)

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
