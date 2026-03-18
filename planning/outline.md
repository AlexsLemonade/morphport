# Planning for PCCMA-nf workflow.

For reference, here is an image of the whiteboard we created when working through the workflow planning.

![Whiteboard image](./whiteboard.png)

I had Claude make a flowchart of this, with a bit of tweaking.
I'm including it here as the first step, but I plan to work on it more to flesh things out and organize it better in future commits.
For now, I just want it recorded here.

```mermaid
flowchart TD
    subgraph INPUT["Input Data"]
        A["Normalized Parquet Files (cell & well level, ~2 files/plate × ~1000 plates)"]
        B["Plate Metadata (date, cell line, timepoint, treatment per well & cell)"]
    end

    INPUT --> S1

    subgraph S1_block["① Reorganize Parquet by cell line, drug & timepoint"]
        S1_in["IN: ..."]
        S1["Reorganize Parquet"]
        S1_out["OUT: ..."]
        S1_in --> S1 --> S1_out
    end

    S1_out --> S1A_in

    subgraph S1A_block["Generate reference lists"]
        S1A_in["IN: ..."]
        S1A["Compound list, cell line list, MOA/drug dict, target/drug dict"]
        S1A_out["OUT: ..."]
        S1A_in --> S1A --> S1A_out
    end

    S1_out --> S2_in

    subgraph S2_block["② QC / Flag Bad Wells"]
        S2_in["IN: ..."]
        S2["Metrics from WayLab output — filter cells by well"]
        S2_out["OUT: ..."]
        S2_in --> S2 --> S2_out
    end

    S2_out --> S3_in
    S2_out --> OUT_in

    subgraph S3_block["③ Visualizations"]
        S3_in["IN: ..."]
        S3["UMAP, hierarchical clustering & heatmap"]
        S3_out["OUT: ..."]
        S3_in --> S3 --> S3_out
    end

    S3_out --> FS{Feature selection?}

    subgraph OUT_block["Output Sets"]
        OUT_in["IN: ..."]
        OUT["Value matrix, UMAP coords, cluster assignments, metadata, neighbor params"]
        OUT_out["OUT: ..."]
        OUT_in --> OUT --> OUT_out
    end

    OUT_out --> BRANCH_in

    subgraph BRANCH_block["Branching Analyses"]
        BRANCH_in["IN: ..."]
        BRANCH["Cancer type/untreated · cell line × MOA · cell line × drug dataset · compound across all cell lines"]
        BRANCH_out["OUT: ..."]
        BRANCH_in --> BRANCH --> BRANCH_out
    end

    BRANCH_out --> CELLS_in

    subgraph CELLS_block["Cell line × Drug/Untreated"]
        CELLS_in["IN: ..."]
        CELLS["Many individual cell sets"]
        CELLS_out["OUT: ..."]
        CELLS_in --> CELLS --> CELLS_out
    end

    FS --> SIM_in

    subgraph SIM_block["Consensus Similarity Matrices"]
        SIM_in["IN: ..."]
        SIM["Compound similarity × cell line+untreated -- Cell line similarity × compound"]
        SIM_out["OUT: ..."]
        SIM_in --> SIM --> SIM_out
    end

    SIM_out --> MAP_in

    subgraph MAP_block["mAP Scoring"]
        MAP_in["IN: ..."]
        MAP["mAP"]
        MAP_out["OUT: ..."]
        MAP_in --> MAP --> MAP_out
    end

    MAP_out --> R1["mAP table by cell line"]
    MAP_out --> R2["mAP by drug"]
```
