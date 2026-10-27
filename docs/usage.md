# AlexsLemonade/morphport: Usage

> _Documentation of pipeline parameters is generated automatically from the pipeline schema and can no longer be found in markdown files._

## Introduction

`AlexsLemonade/morphport` processes Cell Painting profiles for the Pediatric Cancer Cell Morphology Atlas (PCCMA) portal.
Each plate's CellProfiler output is converted to parquet if needed, annotated with platemap metadata, quality filtered, aggregated, and normalized.
Plates are then split by experimental condition and combined into analysis groups for feature selection, embedding (PCA and UMAP), and hierarchical clustering.

We expect most input data to come from the [Cell Painting Gallery](https://github.com/broadinstitute/cellpainting-gallery) (CPG), a public AWS S3 bucket (`s3://cellpainting-gallery`) of Cell Painting datasets.
The pipeline follows CPG directory conventions, so for CPG data the samplesheet often only needs to identify each plate, and the pipeline will find the input files itself.
The important exception is that this does not yet work for parquet input files, as the conventions around the locations of such input files are not yet solidified.
Data stored elsewhere can also be used by providing explicit file paths in the samplesheet.

## Samplesheet input

You will need to create a samplesheet with information about the plates you would like to process.
Use this parameter to specify its location.
It has to be a comma-separated file with a header row, with one row per plate, as shown in the examples below.

```bash
--input '[path to samplesheet file]'
```

### Plates on the Cell Painting Gallery

For data on the Cell Painting Gallery, the `plate`, `project`, `source`, and `batch` columns are enough to locate each plate's files.
These values correspond to components of the CPG directory structure:

```text
s3://cellpainting-gallery/<project>/<source>/workspace/backend/<batch>/<plate>/<plate>.sqlite
s3://cellpainting-gallery/<project>/<source>/workspace/metadata/platemaps/<batch>/barcode_platemap.csv
s3://cellpainting-gallery/<project>/<source>/workspace/metadata/platemaps/<batch>/platemap/<platemap name>.txt
```

When `sqlite_path` is not provided and the project name follows the CPG format (e.g., `cpg0016-jump`), the pipeline reads the plate's CellProfiler SQLite file from the `backend` path above.
When neither `platemap_file` nor `platemaps_dir` is provided, the pipeline looks up the plate in the batch's `barcode_platemap.csv` (matching `Assay_Plate_Barcode` to `plate`) to find the name of its platemap, then reads that platemap from the `platemap` directory.

A samplesheet for CPG plates may look something like the one below:

```csv title="samplesheet.csv"
plate,project,source,batch
2024_02_02_B8A1R2_P3T2,cpg0020-varchamp,broad,2024_02_06_Batch_8
2024_05_17_RC4_Ben_P05,cpg0026-lacoste_haghighi-rare-diseases,broad,2024_05_06_ResponseToReviewers
2024_04_16_RC4_Xexp_FS_P06_R2,cpg0026-lacoste_haghighi-rare-diseases,broad,2024_05_06_ResponseToReviewers
```

The Cell Painting Gallery is public, but reading from it requires either AWS credentials or anonymous S3 access; see [Accessing the Cell Painting Gallery](#accessing-the-cell-painting-gallery).

### Full samplesheet

The samplesheet can include the optional columns below to point to files that are not on the Cell Painting Gallery, to override the default CPG locations, or to start from existing parquet profiles.
Any columns not in the table below are ignored.

<!-- prettier-ignore -->
| Column             | Required | Description
| ------------------ | -------- | --------------
| `plate`            | Yes      | Plate ID (assay plate barcode). Cannot contain spaces.
| `project`          | Yes      | Project name. For CPG data this is the CPG dataset name (e.g., `cpg0016-jump`). Cannot contain spaces.
| `source`           | Yes      | Data source within the project (e.g., `broad`). Cannot contain spaces.
| `batch`            | Yes      | Batch name. Cannot contain spaces.
| `sqlite_path`      | No       | Path to the plate's CellProfiler SQLite file, ending in `.sqlite`. Defaults to the CPG location for CPG projects. Ignored when `cytotable_preset` is `skip`.
| `parquet_path`     | No       | Path or glob (for profiles split across files) to existing parquet profiles, ending in `.parquet`. Required when `cytotable_preset` is `skip`, and ignored otherwise.
| `platemap_file`    | No       | Path to the plate's platemap file. Takes precedence over `platemaps_dir`.
| `platemaps_dir`    | No       | Path to a directory laid out like a CPG `metadata/platemaps` directory, used to look up the platemap as described [above](#plates-on-the-cell-painting-gallery). Defaults to the CPG location for CPG projects.
| `cytotable_preset` | No       | CytoTable preset used to convert the SQLite file to parquet: `cellprofiler_sqlite`, `cellprofiler_sqlite_cpg0016_jump` (default), or `cellprofiler_sqlite_pycytominer`. Use `skip` to skip conversion and read existing profiles from `parquet_path`.

Projects whose names do not follow the CPG format (`cpgNNNN-...`) must provide `sqlite_path` (or `parquet_path`) and one of `platemap_file` or `platemaps_dir`.
For example, a samplesheet mixing CPG data with local parquet profiles might look like this:

```csv title="samplesheet.csv"
plate,project,source,batch,parquet_path,platemaps_dir,cytotable_preset
2024_02_02_B8A1R2_P3T2,cpg0020-varchamp,broad,2024_02_06_Batch_8,,,
MP00000001,my-project,my-lab,Round_1,/data/profiles/MP00000001.parquet,/data/metadata/platemaps,skip
```

An [example samplesheet](../assets/samplesheet.csv) has been provided with the pipeline.

### Plate-level metadata

The samplesheet only identifies each plate and its input files; any other columns are ignored.
Following CPG conventions, any additional metadata should be included as a column in the platemap, even if every well on a plate has the same value.
All platemap columns are added to the annotated profiles as `Metadata_`-prefixed columns and can be used in the [analysis groups file](analysis_groups_format.md).

## Analysis groups

After per-plate processing, profiles are split by experimental condition and combined into analysis groups for feature selection, embeddings, and clustering.
Groups are defined in a YAML (or JSON) file passed with `--analysis_groups_file`, using platemap metadata columns.
If no analysis groups file is given, each level (`cell` and `well`) gets a single group containing all of a project's data.

```bash
--analysis_groups_file '[path to analysis groups file]'
```

See the [analysis groups file format](analysis_groups_format.md) documentation for details, and [`assets/analysis_groups_example.yaml`](../assets/analysis_groups_example.yaml) for an example.

## Running the pipeline

The typical command for running the pipeline is as follows:

```bash
nextflow run AlexsLemonade/morphport \
  --input ./samplesheet.csv \
  --analysis_groups_file ./analysis_groups.yaml \
  --outdir ./results \
  -profile docker
```

This will launch the pipeline with the `docker` configuration profile.
See below for more information about profiles.

Note that the pipeline will create the following files in your working directory:

```bash
work                # Directory containing the nextflow working files
<OUTDIR>            # Finished results in specified location (defined with --outdir)
.nextflow_log       # Log file from Nextflow
# Other nextflow hidden files, eg. history of pipeline runs and old logs.
```

Because input files on the Cell Painting Gallery are read from S3, runs with many plates will transfer a substantial amount of data.
Running the pipeline in AWS (in `us-east-1`, the same region as the Cell Painting Gallery) will generally be much faster than running it locally.

If you wish to repeatedly use the same parameters for multiple runs, rather than specifying each flag in the command, you can specify these in a params file.

Pipeline settings can be provided in a `yaml` or `json` file via `-params-file <file>`.

> [!WARNING]
> Do not use `-c <file>` to specify parameters as this will result in errors. Custom config files specified with `-c` must only be used for [tuning process resource specifications](https://nf-co.re/docs/running/run-pipelines#configuring-pipelines), other infrastructural tweaks (such as output directories), or module arguments (args).

The above pipeline run specified with a params file in yaml format:

```bash
nextflow run AlexsLemonade/morphport -profile docker -params-file params.yaml
```

with:

```yaml title="params.yaml"
input: './samplesheet.csv'
analysis_groups_file: './analysis_groups.yaml'
outdir: './results/'
<...>
```

### Updating the pipeline

When you run the above command, Nextflow automatically pulls the pipeline code from GitHub and stores it as a cached version.
When running the pipeline after this, it will always use the cached version if available - even if the pipeline has been updated since.
To make sure that you're running the latest version of the pipeline, make sure that you regularly update the cached version of the pipeline:

```bash
nextflow pull AlexsLemonade/morphport
```

### Reproducibility

It is a good idea to specify the pipeline version when running the pipeline on your data.
This ensures that a specific version of the pipeline code and software are used when you run your pipeline.
If you keep using the same tag, you'll be running the same version of the pipeline, even if there have been changes to the code since.

First, go to the [AlexsLemonade/morphport releases page](https://github.com/AlexsLemonade/morphport/releases) and find the latest pipeline version - numeric only (eg. `1.3.1`).
Then specify this when running the pipeline with `-r` (one hyphen) - eg. `-r 1.3.1`.
Of course, you can switch to another version by changing the number after the `-r` flag.

This version number will be logged in reports when you run the pipeline, so that you'll know what you used when you look back in the future.

Stochastic steps (such as PCA and UMAP) use a fixed seed, set with `--random_seed`, so repeated runs with the same inputs and parameters should give the same results.

To further assist in reproducibility, you can share and reuse [parameter files](#running-the-pipeline) to repeat pipeline runs with the same settings without having to write out a command with every single parameter.
Because Cell Painting Gallery paths are public, samplesheets that refer only to CPG data can be shared and reused as-is.

> [!TIP]
> If you wish to share such a params file (such as uploading it as supplementary material for academic publications), make sure to NOT include cluster specific paths to files, nor institutional specific profiles.

## Core Nextflow arguments

> [!NOTE]
> These options are part of Nextflow and use a _single_ hyphen (pipeline parameters use a double-hyphen)

### `-profile`

Use this parameter to choose a configuration profile.
Profiles can give configuration presets for different compute environments.

Several generic profiles are bundled with the pipeline which instruct the pipeline to use software packaged using different methods (Docker, Singularity, Podman, Shifter, Charliecloud, Apptainer, Conda) - see below.

> [!IMPORTANT]
> We highly recommend the use of Docker or Singularity containers for full pipeline reproducibility, however when this is not possible, Conda is also supported.

The pipeline also dynamically loads configurations from [https://github.com/nf-core/configs](https://github.com/nf-core/configs) when it runs, making multiple config profiles for various institutional clusters available at run time.
For more information and to check if your system is supported, please see the [nf-core/configs documentation](https://github.com/nf-core/configs#documentation).

Note that multiple profiles can be loaded, for example: `-profile test,docker` - the order of arguments is important!
They are loaded in sequence, so later profiles can overwrite earlier profiles.

If `-profile` is not specified, the pipeline will run locally and expect all software to be installed and available on the `PATH`.
This is _not_ recommended, since it can lead to different results on different machines dependent on the computer environment.

- `test`
  - A profile with a complete configuration for automated testing, using synthetic data in `tests/data/`
  - The test parquet files are not tracked by git and must be generated first (see [`tests/data/README.md`](../tests/data/README.md))
- `stub`
  - A profile for running the pipeline with `-stub`, which creates placeholder outputs to check the workflow logic without processing real data
- `docker`
  - A generic configuration profile to be used with [Docker](https://docker.com/)
- `singularity`
  - A generic configuration profile to be used with [Singularity](https://sylabs.io/docs/)
- `podman`
  - A generic configuration profile to be used with [Podman](https://podman.io/)
- `shifter`
  - A generic configuration profile to be used with [Shifter](https://nersc.gitlab.io/development/shifter/how-to-use/)
- `charliecloud`
  - A generic configuration profile to be used with [Charliecloud](https://charliecloud.io/)
- `apptainer`
  - A generic configuration profile to be used with [Apptainer](https://apptainer.org/)
- `wave`
  - A generic configuration profile to enable [Wave](https://seqera.io/wave/) containers. Use together with one of the above (requires Nextflow `24.03.0-edge` or later).
- `conda`
  - A generic configuration profile to be used with [Conda](https://conda.io/docs/). Please only use Conda as a last resort i.e. when it's not possible to run the pipeline with Docker, Singularity, Podman, Shifter, Charliecloud, or Apptainer.

The pipeline also includes profiles for the Data Lab's own infrastructure (`alsf_batch`, `alsf_testing`, and `alsf_prod`), which run the pipeline on AWS Batch.
These profiles require access to Data Lab AWS resources and are not intended for general use.

### `-resume`

Specify this when restarting a pipeline.
Nextflow will use cached results from any pipeline steps where the inputs are the same, continuing from where it got to previously.
For input to be considered the same, not only the names must be identical but the files' contents as well.
For more info about this parameter, see [this blog post](https://www.nextflow.io/blog/2019/demystifying-nextflow-resume.html).

You can also supply a run name to resume a specific run: `-resume [run-name]`.
Use the `nextflow log` command to show previous run names.

### `-c`

Specify the path to a specific config file (this is a core Nextflow command).
See the [nf-core website documentation](https://nf-co.re/usage/configuration) for more information.

## Custom configuration

### Accessing the Cell Painting Gallery

The Cell Painting Gallery bucket is public, but Nextflow will by default try to use AWS credentials to read from S3.
If you have AWS credentials configured, they will be used to read CPG data and no further configuration is needed.

If you do not have AWS credentials, you can enable anonymous S3 access with a custom config file passed with `-c`.
For example:

```groovy title="cpg.config"
aws {
    client.anonymous = true
    region           = 'us-east-1'
}
```

```bash
nextflow run AlexsLemonade/morphport -profile docker -params-file params.yaml -c cpg.config
```

Note that anonymous access applies to all S3 requests, so it cannot be used if your output or work directory is in a private S3 bucket.

### Resource requests

Whilst the default requirements set within the pipeline will hopefully work for most people and with most input data, you may find that you want to customise the compute resources that the pipeline requests.
Each step in the pipeline has a default set of requirements for number of CPUs, memory and time.
For most of the pipeline steps, if the job exits with any of the error codes specified [here](../conf/base.config) it will automatically be resubmitted once with a higher resource request (2 x original).
If it still fails after the second attempt then the pipeline execution is stopped.

Cell-level steps on plates with many cells, and analysis groups that combine many plates, may need more memory than the defaults.

To change the resource requests, please see the [max resources](https://nf-co.re/docs/running/configuration/nextflow-for-your-system#set-max-resources) and [customise process resources](https://nf-co.re/docs/running/configuration/nextflow-for-your-system#customize-process-resources) section of the nf-core website.

### Custom Containers

In some cases, you may wish to change the container or conda environment used by a pipeline step.
By default, this pipeline uses containers built with [Seqera Containers](https://seqera.io/containers/) from the conda environment files included with each module.

To use a different container from the default container or conda environment specified in a pipeline, please see the [updating tool versions](https://nf-co.re/docs/running/configuration/nextflow-for-your-system#update-tool-versions) section of the nf-core website.

### Custom Tool Arguments

Many options for the tools used in this pipeline (CytoTable, coSMicQC, pycytominer, and the embedding and clustering steps) are exposed as pipeline parameters; see the parameter documentation for details.
For example, quality control thresholds can be changed by providing a JSON file of coSMicQC filter settings with `--cosmicqc_filter_file` (the default is [`assets/cosmicqc_filter_settings.json`](../assets/cosmicqc_filter_settings.json)).

To learn how to provide additional arguments to a particular tool of the pipeline, please see the [customising tool arguments](https://nf-co.re/docs/running/configuration/nextflow-for-your-system#modifying-tool-arguments) section of the nf-core website.

### nf-core/configs

If you are running the pipeline on an institutional cluster, a config profile for your institution may already be available from [nf-core/configs](https://github.com/nf-core/configs); see the [nf-core/configs documentation](https://github.com/nf-core/configs#documentation) for details.

See the main [Nextflow documentation](https://www.nextflow.io/docs/latest/config.html) for more information about creating your own configuration files.

## Running in the background

Nextflow handles job submissions and supervises the running jobs.
The Nextflow process must run until the pipeline is finished.

The Nextflow `-bg` flag launches Nextflow in the background, detached from your terminal so that the workflow does not stop if you log out of your session.
The logs are saved to a file.

Alternatively, you can use `screen` / `tmux` or similar tool to create a detached session which you can log back into at a later time.
Some HPC setups also allow you to run nextflow within a cluster job submitted your job scheduler (from where it submits more jobs).

## Nextflow memory requirements

In some cases, the Nextflow Java virtual machines can start to request a large amount of memory.
We recommend adding the following line to your environment to limit this (typically in `~/.bashrc` or `~./bash_profile`):

```bash
NXF_OPTS='-Xms1g -Xmx4g'
```
