# DDOT Public Space Permit — FHWA Functional Class Enrichment

**CS-12 Data Scientist | Python take-home assessment**  
**Platform:** Windows 11, Python 3.10+

## 1. Overview

This Python workflow retrieves public-space occupancy permit **locations** from the DC GIS/DDOT TOPS ArcGIS REST service and enriches them with Federal Highway Administration (FHWA) roadway functional classifications. It preserves **one row per permit location** and calculates the **highest-priority available FHWA class across all locations within a permit application** (`TrackingNumber`). Lower numeric FHWA codes correspond to higher-priority road classes (1 = Interstate; 7 = Local).

The project targets permits associated with **DC Water, PEPCO, and Washington Gas**, for **Construction Staging Area** and **Mobile Crane Work Zone** event types, where either the application date or issue date is on/after January 1, 2026. Utility identification is based on explicit utility-name terms within available applicant, permittee, and owner fields. Aliases not represented by those fields/terms may not be captured.

The current extraction uses an **open-ended** date predicate (from January 1, 2026 onward), not a strict December 31, 2026 cutoff. For a strictly bounded calendar-year analysis, apply an upper-bound date condition in `extract_permits.py` and rerun subsequent steps.

## 2. Files and folder structure

Place all three scripts **in the same root directory**, alongside `requirements.txt` and this README. Their default file paths are relative to the location of each script.

```text
DDOT_Python_Assessment/
├── README.md
├── requirements.txt
├── extract_permits.py
├── enrich_road_classes.py
├── resolve_exceptions.py
├── output/
│   ├── permits_extracted.csv
│   ├── permits_enriched.csv                  # MAIN SUBMISSION CSV
│   ├── permits_enriched_review.csv           # unresolved-location subset
│   ├── exception_candidates.csv              # alternative roads, not approved
│   └── exception_investigation_summary.csv
└── documentation/
    └── DDOT_GIS_Assessment_Methodology_and_Manual_Review.docx  # optional supporting report
```

`output/` is created automatically as necessary by the scripts. Supporting files beyond those shown may be produced during exception investigation.

## 3. Set up on Windows 11

1. Install Python 3.10 or newer, with the Python launcher (`py`) available.
2. Extract the submission ZIP into a writable folder.
3. Open that folder in **VS Code**, then choose **Terminal > New Terminal** (PowerShell).
4. Create an isolated environment and install the dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

The project depends on `requests` and `pandas`; see `requirements.txt` for versions. **Internet access is required** to query the DC government ArcGIS endpoints. ArcGIS Pro is **not required** to execute the Python pipeline; it was used separately for visual quality assurance.

For all commands below, replace `py` with `.\.venv\Scripts\python.exe` to explicitly use the installed environment.

## 4. Run the scripts (in order)

### Step 1 — Extract permit locations

```powershell
py extract_permits.py
```

**Output:** `output/permits_extracted.csv`.

The script filters the live TOPS service, paginates results, and checks record counts and duplicate `OBJECTID` values. It retains location-level records rather than collapsing multiple locations into one permit row.

### Step 2 — Test roadway enrichment on a small sample

```powershell
py enrich_road_classes.py --limit 10
```

**Outputs:** `output/permits_enriched_preview.csv` and `output/permits_enriched_preview_review.csv`.

This verifies service access and matching logic without processing all records. **Do not submit the preview as the final output:** its permit-level highest class is intentionally not calculated, because other locations for the same application could be absent from a sample.

### Step 3 — Enrich the full dataset

```powershell
py enrich_road_classes.py --all --checkpoint-every 25
```

**Outputs:** `output/permits_enriched.csv` (primary deliverable) and `output/permits_enriched_review.csv` (unresolved records).

The script queries DDOT's Linear Referencing System (LRS) and roadway functional-class data, evaluates roadway association using available spatial/road-name/block evidence, records the per-location FHWA class where supported, and computes `HighestPermitFHWAClass` for the full application. It checkpoints its progress during long runs.

If **a full run has been interrupted** and the existing output contains a valid checkpoint, resume it with:

```powershell
py enrich_road_classes.py --all --resume --checkpoint-every 25
```

**Warning:** Starting again with `--all` **without** `--resume` will replace the existing enriched output. Back up the supplied final CSV before reproducing the pipeline.

### Step 4 — Investigate unresolved cases (optional supporting analysis)

```powershell
py resolve_exceptions.py --all --checkpoint-every 10
```

**Outputs include:** `output/exception_candidates.csv` and `output/exception_investigation_summary.csv`.

This script researches alternative nearby roadway segments and ranks them using spatial proximity, route-name/block agreement, and related evidence. **Candidates are suggestions for review, not accepted classifications.** The exception script does **not** overwrite `permits_enriched.csv` or automatically fill missing FHWA values.

If this investigation is interrupted, resume with:

```powershell
py resolve_exceptions.py --all --resume --checkpoint-every 10
```

The exception script uses a progress checkpoint; if one already exists, start with `--resume` instead of launching a new `--all` run. Run only one exception investigation at a time.

## 5. How to read the final CSV

The primary CSV retains all extracted source permit-location fields and appends enrichment fields:

| Column                                    | Meaning                                                                                      |
| ----------------------------------------- | -------------------------------------------------------------------------------------------- |
| `TrackingNumber`                          | Permit/application identifier; can repeat across multiple locations                          |
| `LocationID` / `OBJECTID`                 | Location identifiers                                                                         |
| `MatchedRoadName`                         | Roadway selected by the automated matching process, if supported                             |
| `FHWAClass`                               | Location-level FHWA numeric class (1–7); blank if unresolved                                 |
| `FHWAClassName`                           | Human-readable class name                                                                    |
| `HighestPermitFHWAClass`                  | Lowest available numeric FHWA code across classified locations for the same `TrackingNumber` |
| `MatchStatus`, `MatchMethod`, `MatchNote` | Matching outcome, method and diagnostic rationale                                            |
| `StreetOffsetMeters`, `AlleyOffsetMeters` | LRS location-distance evidence, when available                                               |

**Important:** If some locations in an application are unresolved, its `HighestPermitFHWAClass` is the highest-priority **known** class, **not necessarily the true final highest**. If no locations in an application are classified, the application-level class stays blank. A lower numeric code indicates a higher functional road class.

## 6. Results of the provided analysis snapshot

| Metric                                                      |            Result |
| ----------------------------------------------------------- | ----------------: |
| Permit-location records                                     |         **4,926** |
| Distinct applications (`TrackingNumber`)                    |         **3,102** |
| Locations with assigned FHWA class                          | **4,647 (94.3%)** |
| Locations with missing/unresolved FHWA class                |    **279 (5.7%)** |
| Unresolved locations investigated for other road candidates |           **279** |
| Unresolved locations with at least one candidate            |           **277** |
| Alternative roadway candidates returned                     |         **1,113** |
| Locations with no candidate found in investigation          |             **2** |

These are **snapshot counts**, not promised counts for future runs against the live REST service. The 94.3% figure measures **classification coverage, not verified accuracy**.

## 7. Why are FHWA fields blank for some rows?

All 4,926 source locations are preserved, even if their correct roadway cannot be established reliably. Reasons include an address inconsistent with the closest mapped road, significant point-to-road offset, alleys without a functional class, unusual bridge/intersection geometry, and missing or conflicting route information. The original run left **279 locations** unresolved rather than guessing. The review subset (`permits_enriched_review.csv`) is already contained within the main CSV and **must not be appended** to it, or duplicate location records will result.

Two sample locations were examined visually in ArcGIS Pro:

- **Roosevelt Bridge:** Interstate 66 provided strong evidence of FHWA **Class 1** for the bridge location, but the candidate was **not applied as a verified correction** to the primary output.
- **3333 Georgia Avenue NW:** An address-consistent Georgia Avenue segment has FHWA **Class 3**, but the plotted location is closer to Morton Street/an alley. The occupancy measurements show curb/travel-lane use without conclusively proving which roadway is occupied. It remains **unresolved**.

Manual review was stopped after these two illustrative cases due to the substantial remaining case-by-case evidence required. The decision preserves a reproducible, defensible partial output rather than introducing unsupported roadway classifications. The assessment explicitly allows partially completed work accompanied by an explanation of remaining steps.

## 8. Assumptions, limitations, and next steps

- **Utility matching:** Names are located using explicit fields and a set of known strings, not a complete utility/contractor identity registry.
- **Geometry and classification:** Proximity alone does not establish which roadway an occupancy permit affects. Nearby roads and alleys can produce conflicting evidence.
- **FHWA priority:** The **minimum numeric code** is the highest-priority functional class; this convention is used in application-level aggregation.
- **Missing values:** Unresolved location and application classes remain blank; they are not treated as Class 7 or zero.
- **Live data:** Counts, permits and matching results may differ when the public service is rerun.
- **Further work:** Verify the unresolved locations in ArcGIS Pro or against authoritative permit geometry; use the MAR/SubBlock approach as an independent check where appropriate; record approved roadway IDs and evidence; apply only reviewed corrections; recalculate application-level highest classes; and rerun all completeness/uniqueness checks.

## 9. Data sources

- DDOT occupancy permit locations: <https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_APPS/DDOT_TOPS/MapServer/24>
- DDOT LRS roadway operation: <https://rh.dcgis.dc.gov/dcgis/rest/services/DDOT/RoadwayBlocks/MapServer/exts/DdotLrsExtensions/getPointOnRoute>
- Roadway functional classification: <https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Transportation_WebMercator/MapServer/48>

**Submission note:** `output/permits_enriched.csv` is the requested single location-level dataset. The review and candidate files are supporting evidence, not additional rows to merge into the primary deliverable.
