import pypandoc, os
readme = r"""# DDOT CS-12 Data Scientist Take-Home Assessment

**Candidate:** Lokesh K. Rajaram  
**Organization:** District Department of Transportation (DDOT), Washington, DC  
**Assessment:** CS-12 Data Scientist — GIS, Python, and Tableau  
**Date:** October 2026

## Overview

This repository documents my approach to three DDOT take-home assessment tasks: Python-based roadway functional-class enrichment for permits, ArcGIS Pro spatial integration of permits and Cityworks requests, and Tableau analysis of construction-permit processing times.

The work emphasizes reproducible processing, geographic and relational data validation, transparent assumptions, and clear communication of incomplete or provisional results. The three tasks are related by their transportation-data context, but their outputs and populations are **not interchangeable**.

> **Scope note:** This README reconstructs the workflow and verified figures from assessment work logs and project documentation. Individual source files, scripts, geodatabases, and workbooks should be committed only after confirming their exact names, contents, permissions, and reproducibility. Counts reflect the respective task outputs, not a single common dataset.

## Assessment summary

| Workstream | Objective | Main output | Validated result |
|---|---|---|---|
| Python | Enrich permit application locations with roadway functional classes and determine the highest class associated with each application | Enriched CSV and exception/review outputs | 4,926 locations; 4,647 classified; 279 unresolved |
| GIS / ArcGIS Pro | Link permits and Cityworks requests to roadway segments and summarize activity spatially | `FINAL_Roadways_Enriched` and `FINAL_Permits_Snapped_Provisional` | 23,104 roadway segments; 6,880 provisional permit matches; 651 matched Cityworks requests |
| Tableau | Analyze FY2026 construction-permit approval duration and build an interactive dashboard | `DDOT_FY2026_Construction_Permit_Analysis.twbx` | 5,378 unique permits; mean 11.40 business days |

## 1. Python — roadway functional-class enrichment

### Business question

For each permit application location, determine its roadway functional class and the highest functional class associated with the permit application. The requested output is **one row per permit application location**, not one row per potential roadway match.

### Approach

1. Examined the permit and roadway inputs, identifiers, address/location fields, and functional-class attributes.
2. Developed Python extraction and enrichment scripts to standardize records and associate permit locations with roadway classification data.
3. Built a candidate-matching and exception-review process rather than silently assigning uncertain road classes.
4. Investigated selected exception candidates with manual GIS review to demonstrate how uncertain cases could be resolved.
5. Retained unresolved values as blank rather than manufacturing classifications.
6. Produced the primary enriched output and supplementary review material, along with documentation of assumptions, blockers, and stopping criteria.

### Results

- **4,926** permit application locations in the reported output.
- **4,647** classified locations.
- **279** unresolved locations (approximately **5.7%**).
- **1,113** candidate roadway records were reported during the investigation/review workflow; this figure is **not** the number of final permit locations.

### Important limitations

The assessment did not establish defensible classifications for all records. Ambiguous or unmatched locations were preserved for review rather than forcibly assigned to a roadway. The manual review was intentionally limited to representative exception candidates to demonstrate a repeatable investigative approach; it was not represented as exhaustive validation.

### Scripts and outputs to include (verify exact local names)

- `extract_permits.py`
- `enrich_road_classes.py`
- `resolve_exceptions.py`
- Primary enriched permit CSV, including application-location functional class and application-level highest functional class
- Exception/review CSV and, where retained, candidate-road investigation CSV
- `requirements.txt` and a task-specific README/methodology document

**Reproduction:** Install the packages in the actual `requirements.txt`, inspect the script inputs and configuration, then run the scripts in their documented order. Paths, source access, and expected input schemas must be supplied in the task-specific instructions; this README does not assert that the scripts run without configuration.

## 2. ArcGIS Pro — roadway, permit, and Cityworks integration

### Business question

Create a roadway-level geographic dataset showing where construction permits and Cityworks requests occur, while documenting the confidence and limitations of linking point events to roadway segments.

### Data preparation and matching

1. Prepared and validated the roadway layer (`Roadways_Filtered`) with **23,104** unique `ROAD_SEG_ID` values, **zero null** identifiers, and **zero duplicates**.
2. Inspected permit identifier fields, including `StreetSegID` and `RoadwaySegID`; many permit records lacked `RoadwaySegID`, so identifier-only matching was insufficient.
3. Evaluated proximity and street-name agreement, using address and roadway name normalization for quality assurance.
4. Used planar nearest-road searches with a **100-foot search radius** for candidate evaluation. Distance units and projected-coordinate-system assumptions must be preserved when reproducing the analysis.
5. Applied a **provisional permit acceptance rule** requiring `NAME_MATCH` and `NEAR_DIST <= 20 ft`. This produced **6,880** provisional permit-to-road assignments in `FINAL_Permits_Snapped_Provisional`.
6. Recorded **372 distance exceptions** and **872 street-name review exceptions** in the documented matching workflow. These exception groups should not automatically be treated as disjoint.
7. Matched Cityworks requests to roadways with a rank-1 nearest-candidate approach within the 100-foot search distance. Of **698** eligible requests, **651** matched and **47** remained unmatched.
8. Aggregated matched records by roadway, carrying counts and latest dates into the enriched roadway layer.
9. Created a final ArcGIS Pro map layout to communicate the roadway activity and provisional match output.

### Principal outputs

| Dataset | Description |
|---|---|
| `FINAL_Roadways_Enriched` | Roadway-level output with `ROAD_SEG_ID`, `PERMIT_COUNT`, `MAX_PERMIT_EXP`, `REQUEST_COUNT`, `MAX_REQUEST_DATE`, and `Has_Both` |
| `FINAL_Permits_Snapped_Provisional` | 6,880 permit points assigned to roadways under the documented provisional matching rule |
| `SUM_Permits_By_Road` | 1,947 roadways with matched permits; summed permit frequency 6,880 |
| `SUM_Cityworks_By_Road` | 547 roadways with matched requests; summed request frequency 651 |
| `MATCH_Cityworks_Details` | 651 matched request records, with populated roadway identifiers and initiation dates |

### Final validation

| Check | Result |
|---|---:|
| Roadway segments | 23,104 |
| Permit-only roads | 1,865 |
| Cityworks-only roads | 465 |
| Roads with both | 82 |
| Roads with neither | 20,692 |
| Total permit counts across roads | 6,880 |
| Total request counts across roads | 651 |
| Incorrect `Has_Both` flags | 0 |
| Duplicate `ROAD_SEG_ID` values | 0 |
| Reported date-consistency exceptions | 0 |
| Null permit/request counts in final layer | 0 |

The four roadway categories reconcile: `1,865 + 465 + 82 + 20,692 = 23,104`. The count summaries also reconcile to the matched-record totals.

### Why these are *provisional* matches

A close spatial distance and normalized street-name agreement improve confidence, but do not prove that a permit belongs to a specific roadway segment. Intersections, parallel roads, ambiguous addresses, geocoding error, and source-data discrepancies can create false associations. Unmatched Cityworks requests and rejected permit candidates should remain visible as limitations, not be represented as successfully mapped activity.

### ArcGIS project packaging

Include the `.aprx` project, required file geodatabase(s), any necessary local source tables/layers, final map PDF, and task-specific documentation. Confirm that all project paths resolve after extracting the ZIP on **Windows 11**. An `.aprx` alone is not self-contained.

## 3. Tableau — FY2026 construction-permit analysis

### Business question

Analyze construction-permit approval durations and present interpretable KPIs and trends in a Tableau dashboard.

### Python-supported preparation

1. Retrieved permit records using an API extraction workflow.
2. Encountered an HTTP **414 (URI Too Long)** response during extraction and adjusted requests to use **150-record batches**.
3. Audited extracted records and handled repeated records so that the Tableau analysis used the intended **permit-level grain**.
4. Calculated business-day durations, excluding weekends and the applicable District of Columbia holidays.
5. Performed validation and permit classification for the Tableau-ready dataset.
6. Created the supporting analysis files used to populate Tableau.

Relevant supporting scripts reported in the task documentation:

- `step1_extract_audit.py`
- `step2_business_days.py`
- `step4_classify_permits.py`

The extraction-to-permit-level deduplication process was **not fully delivered as one standalone, executable end-to-end script**. Do not claim complete one-command reproducibility until that gap is addressed.

### Tableau dashboard development

- Connected the prepared data to Tableau.
- Created the required worksheets and assembled a dashboard.
- Built permit-count and average-business-day KPI displays.
- Added analytical charts, text, and dashboard layout elements.
- Iteratively corrected dashboard layout and formatting problems, including container positioning, KPI alignment, text placement, and number displays that rendered as `###` when the available space was insufficient.
- Checked headline values with filters cleared before packaging the workbook.

### Verified dashboard KPIs

| KPI | Value |
|---|---:|
| Unique permits | **5,378** |
| Mean processing duration | **11.40 business days** |

**Date caveat:** Validation involving `LastApprovalDate` remained an open limitation in the assessment documentation. Do not imply that all source-date semantics were conclusively resolved. The interpretation of the duration metric should be read alongside the task-specific methodology.

### Main deliverable

`DDOT_FY2026_Construction_Permit_Analysis.twbx`

Supporting analysis dependencies included Python packages such as `requests`, `pandas`, and `numpy`. The final submission recommendation was to include the packaged workbook, task-specific README/methodology, and useful supporting scripts and audit outputs, while omitting unnecessary full raw extracts.

## 4. Challenges and how they were addressed

| Challenge | Response |
|---|---|
| Missing or inconsistent roadway identifiers | Investigated identifier completeness and used documented spatial/name-based matching with review flags |
| Ambiguous permit-to-road associations | Kept accepted GIS assignments explicitly provisional and recorded exceptions |
| Unmatched Cityworks requests | Reported the 47 unmatched requests rather than inflating matched totals |
| Unresolved Python roadway classes | Left 279 locations unresolved and documented the review approach |
| HTTP 414 during Tableau extraction | Reduced extraction requests to 150-record batches |
| Repeated permit source records | Prepared data at permit grain for dashboard KPIs |
| Tableau layout issues | Adjusted containers, alignment, spacing, and text formatting |
| Uncertain approval-date interpretation | Preserved `LastApprovalDate` validation as a limitation |

## 5. Cross-cutting QA principles

- Confirm the **grain** of each output before aggregating or joining.
- Preserve stable IDs and check for duplicate or null keys.
- Reconcile summary totals with detail records.
- Separate matched, unmatched, reviewed, and provisional observations.
- Check spatial units and distance thresholds before interpreting near matches.
- Validate Tableau metrics independently of dashboard appearance.
- Record assumptions and unresolved cases rather than masking them.
- Distinguish an analytical recommendation from a verified operational fact.

## 6. Repository layout

The following is a **recommended GitHub layout**, not a claim that every listed path already exists:

```text
DDOT-CS12-Data-Scientist-Assessment/
├── README.md
├── python/
│   ├── README.md
│   ├── scripts/
│   │   ├── extract_permits.py
│   │   ├── enrich_road_classes.py
│   │   └── resolve_exceptions.py
│   └── outputs/                 # Approved / sanitized samples only
├── gis/
│   ├── README.md
│   ├── maps/                    # Final export, if sharing permitted
│   └── methodology/
├── tableau/
│   ├── README.md
│   ├── DDOT_FY2026_Construction_Permit_Analysis.twbx
│   └── supporting_analysis/
│       ├── step1_extract_audit.py
│       ├── step2_business_days.py
│       └── step4_classify_permits.py
└── docs/
    └── assessment-methodology/
```

Only publish artifacts that DDOT permits you to share. Before making the repository public, review the assessment's confidentiality terms, data licensing, any personal information in source records, embedded credentials, local file paths, and any restricted data in geodatabases or packaged Tableau workbooks. If publication rights are unclear, keep the repository private or publish a methodology-only version with synthetic or authorized data.

## 7. Reproducibility and environment

- **Target platform:** Windows 11, per the assessment guidance.
- **GIS:** ArcGIS Pro; project dependencies and spatial reference details must accompany the analysis.
- **Dashboard:** Tableau Desktop / packaged workbook.
- **Python:** Use the package versions documented in the actual task requirements; supporting analysis used `requests`, `pandas`, and `numpy`.
- **Inputs:** Source access, schemas, dates, paths, and permitted data-sharing scope must be verified before another user can rerun the workflows.

This is a methodological record of the completed work. It is **not** a guarantee that every workstream can be reproduced end-to-end from this repository without the original authorized datasets and environment-specific setup.

## 8. Submission format

The DDOT guidance requested a self-contained parent ZIP with separate ZIP deliverables for the assessment areas completed, named according to the candidate-name convention, and compatible with Windows 11. The designated submission location was the Box portal supplied by DDOT; GitHub was optional and supplementary.

Illustrative package names:

```text
Lokesh_K_Rajaram_CS12_DataScientist_Tests.zip
  Lokesh_K_Rajaram_Python.zip
  Lokesh_K_Rajaram_GIS.zip
  Lokesh_K_Rajaram_Tableau.zip
```

The actual task-specific instructions govern the mandatory contents of each archive.

## 9. Lessons learned

This assessment reinforced that public-sector data science is not just producing a map, a script, or a dashboard. It requires defensible joins, a clear understanding of record grain, spatial uncertainty management, traceable transformations, and communication of what the results can and cannot support. Keeping unresolved cases explicit made the deliverables more auditable and helped distinguish verified results from provisional analysis.

---

**Author:** Lokesh K. Rajaram  
**Purpose:** Portfolio documentation of the DDOT CS-12 Data Scientist take-home assessment.  
**Disclaimer:** This is an independent candidate project record, not an official DDOT publication or endorsement.
"""
path='/mnt/data/DDOT_CS12_Assessment_GitHub_README.md'
pypandoc.convert_text(readme, 'md', format='md', outputfile=path, extra_args=['--standalone'])
print(path, os.path.getsize(path))
