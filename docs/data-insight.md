# DataInsightAgent

## Scope

DataInsightAgent profiles local CSV, TSV, and basic XLSX files without relying on a cloud data service. It preserves source files and writes only a session-isolated dataset profile plus local SVG chart artifacts.

## Tool interface

| Tool | Result |
| --- | --- |
| `data_profile` | Row/column counts, inferred types, missingness, uniqueness, numeric summaries |
| `data_analyze` | Deterministic duplicate-row, missing-data, negative-value, and outlier risks |
| `data_chart` | Local SVG bar chart for a selected categorical/numeric pair |

## Evaluation evidence

Record profile latency, analysis latency, chart latency, risk precision against labeled sample data, and cross-session isolation. The deterministic baseline makes numeric findings independently reproducible; an upstream model can later explain the findings but must not replace their calculation.
