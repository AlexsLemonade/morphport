# AlexsLemonade/morphport

[![Open in GitHub Codespaces](https://img.shields.io/badge/Open_In_GitHub_Codespaces-black?labelColor=grey&logo=github)](https://github.com/codespaces/new/AlexsLemonade/morphport)
[![GitHub Actions CI Status](https://github.com/AlexsLemonade/morphport/actions/workflows/nf-test.yml/badge.svg)](https://github.com/AlexsLemonade/morphport/actions/workflows/nf-test.yml)
[![GitHub Actions Linting Status](https://github.com/AlexsLemonade/morphport/actions/workflows/linting.yml/badge.svg)](https://github.com/AlexsLemonade/morphport/actions/workflows/linting.yml)[![Cite with Zenodo](http://img.shields.io/badge/DOI-10.5281/zenodo.XXXXXXX-1073c8?labelColor=000000)](https://doi.org/10.5281/zenodo.XXXXXXX)
[![nf-test](https://img.shields.io/badge/unit_tests-nf--test-337ab7.svg)](https://www.nf-test.com)

[![Nextflow](https://img.shields.io/badge/version-%E2%89%A525.10.4-green?style=flat&logo=nextflow&logoColor=white&color=%230DC09D&link=https%3A%2F%2Fnextflow.io)](https://www.nextflow.io/)
[![nf-core template version](https://img.shields.io/badge/nf--core_template-4.2.1-green?style=flat&logo=nfcore&logoColor=white&color=%2324B064&link=https%3A%2F%2Fnf-co.re)](https://github.com/nf-core/tools/releases/tag/4.2.1)
[![run with conda](http://img.shields.io/badge/run%20with-conda-3EB049?labelColor=000000&logo=anaconda)](https://docs.conda.io/en/latest/)
[![run with docker](https://img.shields.io/badge/run%20with-docker-0db7ed?labelColor=000000&logo=docker)](https://www.docker.com/)
[![run with singularity](https://img.shields.io/badge/run%20with-singularity-1d355c.svg?labelColor=000000)](https://sylabs.io/docs/)
[![Launch on Seqera Platform](https://img.shields.io/badge/Launch%20%F0%9F%9A%80-Seqera%20Platform-%234256e7)](https://cloud.seqera.io/launch?pipeline=https://github.com/AlexsLemonade/morphport)

## Introduction

**AlexsLemonade/morphport** is a bioinformatics pipeline to support the Pediatric Cancer Cell Morphology Atlas (PCCMA) project.
The pipeline is designed to process Cell Painting data for the PCCMA project and prepare the results for display on the PCCMA portal.

The pipeline takes Cell Painting data from imaging experiments, converts and annotates measurements, performs quality control and aggregation, normalizes and selects features, and produces dimensionality-reduced embeddings for visualization on the PCCMA portal.

```mermaid
flowchart TD
    INPUT([Input:\nsamplesheet & data files])

    subgraph per_plate ["Per plate"]
        CYTOTABLE["CYTOTABLE_CONVERT\nSQLite → Parquet\n(optional)"]
        ANNOTATE[PYCYTOMINER_ANNOTATE\nAdd platemap metadata]
        FILTER[COSMICQC_FILTER\nRemove low-quality cells]
        AGGREGATE[PYCYTOMINER_AGGREGATE\nCell → well level]
        NORMALIZE["PYCYTOMINER_NORMALIZE\nNormalize features\n(optional)"]

        CYTOTABLE --> ANNOTATE
        ANNOTATE --> FILTER
        FILTER -->|cell level| NORMALIZE
        FILTER --> AGGREGATE
        AGGREGATE -->|well level| NORMALIZE
    end

    subgraph per_group ["Per analysis group"]
        FEATURE_SELECT[PYCYTOMINER_FEATURE_SELECT\nSelect informative features]
        EMBEDDINGS[COMPUTE_EMBEDDINGS\nPCA + UMAP]
        CLUSTERING[HIERARCHICAL_CLUSTERING_SAMPLES\nCluster samples by PCA]
        CLUSTER_FEATURES[HIERARCHICAL_CLUSTERING_FEATURES\nCluster features]
        FEATURE_SELECT --> EMBEDDINGS
        EMBEDDINGS --> CLUSTERING
        FEATURE_SELECT --> CLUSTER_FEATURES
    end

    style per_plate fill:#dbeafe,stroke:#93c5fd,rx:10,ry:10
    style per_group fill:#dcfce7,stroke:#86efac,rx:10,ry:10

    INPUT --> per_plate

    PARTITION["PARTITION_CONDITIONS\nSplit by conditions"]
    GROUP["GROUP\nCombine by analysis groups\n(e.g., cell line)"]

    per_plate -->|"Plate 1"| PARTITION
    per_plate -->|"Plate 2"| PARTITION
    per_plate -->|"..."| PARTITION
    per_plate -->|"Plate N"| PARTITION
    PARTITION --> GROUP
    PARTITION --> GROUP
    PARTITION --> GROUP
    PARTITION --> GROUP
    PARTITION --> GROUP
    PARTITION --> GROUP
    GROUP -->|"Group 1"| per_group
    GROUP -->|"Group 2"| per_group
    GROUP -->|"..."| per_group
    GROUP -->|"Group M"| per_group
    per_group --> OUTPUT([Parquet files,\nembeddings & clustering])
```

## Usage

> [!NOTE]
> If you are new to Nextflow and nf-core, please refer to [this page](https://nf-co.re/docs/get_started/environment_setup/overview) on how to set-up Nextflow. Make sure to [test your setup](https://nf-co.re/docs/get_started/run-your-first-pipeline) with `-profile test` before running the workflow on actual data.

<!-- TODO nf-core: Describe the minimum required steps to execute the pipeline, e.g. how to prepare samplesheets.
     Explain what rows and columns represent. For instance (please edit as appropriate):

First, prepare a samplesheet with your input data that looks as follows:

`samplesheet.csv`:

```csv
sample,fastq_1,fastq_2
CONTROL_REP1,AEG588A1_S1_L002_R1_001.fastq.gz,AEG588A1_S1_L002_R2_001.fastq.gz
```

Each row represents a fastq file (single-end) or a pair of fastq files (paired end).

-->

Now, you can run the pipeline using:

<!-- TODO nf-core: update the following command to include all required parameters for a minimal example -->

```bash
nextflow run AlexsLemonade/morphport \
   -profile <docker/singularity/.../institute> \
   --input samplesheet.csv \
   --outdir <OUTDIR>
```

> [!WARNING]
> Please provide pipeline parameters via the CLI or Nextflow `-params-file` option. Custom config files including those provided by the `-c` Nextflow option can be used to provide any configuration _**except for parameters**_; see [docs](https://nf-co.re/docs/running/run-pipelines#using-parameter-files).

## Credits

AlexsLemonade/morphport was originally written by Joshua A. Shapiro.

We thank the following people for their extensive assistance in the development of this pipeline:

<!-- TODO nf-core: If applicable, make list of people who have also contributed -->

## Contributions and Support

If you would like to contribute to this pipeline, please see the [contributing guidelines](docs/CONTRIBUTING.md).

## Citations

<!-- TODO nf-core: Add citation for pipeline after first release. Uncomment lines below and update Zenodo doi and badge at the top of this file. -->
<!-- If you use AlexsLemonade/morphport for your analysis, please cite it using the following doi: [10.5281/zenodo.XXXXXX](https://doi.org/10.5281/zenodo.XXXXXX) -->

<!-- TODO nf-core: Add bibliography of tools and data used in your pipeline -->

An extensive list of references for the tools used by the pipeline can be found in the [`CITATIONS.md`](CITATIONS.md) file.

This pipeline uses code and infrastructure developed and maintained by the [nf-core](https://nf-co.re) community, reused here under the [MIT license](https://github.com/nf-core/tools/blob/main/LICENSE).

> **The nf-core framework for community-curated bioinformatics pipelines.**
>
> Philip Ewels, Alexander Peltzer, Sven Fillinger, Harshil Patel, Johannes Alneberg, Andreas Wilm, Maxime Ulysse Garcia, Paolo Di Tommaso & Sven Nahnsen.
>
> _Nat Biotechnol._ 2020 Feb 13. doi: [10.1038/s41587-020-0439-x](https://dx.doi.org/10.1038/s41587-020-0439-x).
