"""DDOT Tableau assessment — Step 1: extract and audit FY26 permit candidates.

Install: python -m pip install requests pandas
Run:     python step1_extract_audit.py

This script deliberately does NOT calculate approval days or declare eligibility final.
It collects records satisfying the explicit application/issue dates and the API's
New permit code, regardless of current status, for transparent examination.
"""
from pathlib import Path
import json
import time
import requests
import pandas as pd

BASE = "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_APPS/DDOT_TOPS/MapServer/23/query"
OUT = Path(__file__).resolve().parent / "output"
OUT.mkdir(parents=True, exist_ok=True)
# Keep GET URLs well below typical server/proxy request-URI limits.
# ArcGIS maxRecordCount=2000 is a RESPONSE limit, not a URL-length allowance.
PAGE_SIZE = 150

# Dates interpreted in the layer's America/New_York timezone.
# Upper boundary is exclusive, so FY26 is Oct 1, 2025 - Sep 30, 2026.
WHERE = (
    "ApplicationDate >= TIMESTAMP '2025-10-01 00:00:00' "
    "AND IssueDate >= TIMESTAMP '2025-10-01 00:00:00' "
    "AND IssueDate < TIMESTAMP '2026-10-01 00:00:00' "
    "AND PermitType = 0"
)
FIELDS = [
    "OBJECTID", "TrackingNumber", "PermitNumber", "StatusDescription",
    "ApplicationDate", "IntakeDate", "ReadyForReviewDate", "LastApprovalDate",
    "IssueDate", "PermitType", "PermitTypeDescription", "TypeDetailNames",
    "IsExcavation", "IsPaving", "IsOccupancy", "IsLandscaping",
    "WorkLocationWard", "FiscalYear", "DaysToApprove", "DaysToIssue"
]

session = requests.Session()
session.headers.update({"User-Agent": "DDOT-Tableau-assessment-data-review/1.0"})

def query(parameters):
    for attempt in range(5):
        try:
            response = session.get(BASE, params={"f": "json", **parameters}, timeout=60)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise RuntimeError(f"ArcGIS error: {data['error']}")
            return data
        except (requests.RequestException, ValueError) as exc:
            if attempt == 4:
                raise RuntimeError(f"Failed to query DDOT service: {exc}") from exc
            time.sleep(2 ** attempt)
    raise RuntimeError("Unexpected query failure")

print("Requesting server-side count...")
count = query({"where": WHERE, "returnCountOnly": "true"})["count"]
print(f"Records matching preliminary filters: {count:,}")

# Obtain OBJECTIDs first to avoid skipping records due to unstable offsets.
# Each GET request below includes at most 150 IDs to avoid HTTP 414 errors.
ids_result = query({"where": WHERE, "returnIdsOnly": "true"})
ids = sorted(ids_result.get("objectIds") or [])
if len(ids) != count:
    raise RuntimeError(f"Count mismatch: count={count}, ids={len(ids)}. Retry extraction.")

records = []
for pos in range(0, len(ids), PAGE_SIZE):
    chunk = ids[pos:pos + PAGE_SIZE]
    result = query({
        "objectIds": ",".join(map(str, chunk)),
        "outFields": ",".join(FIELDS),
        "returnGeometry": "false",
        "orderByFields": "OBJECTID ASC"
    })
    features = result.get("features", [])
    if result.get("exceededTransferLimit") or len(features) != len(chunk):
        raise RuntimeError(f"Incomplete batch starting at {pos}; expected {len(chunk)}, got {len(features)}")
    records.extend(feature["attributes"] for feature in features)
    print(f"Fetched {len(records):,} of {len(ids):,}")

raw = pd.DataFrame.from_records(records, columns=FIELDS)
if raw.OBJECTID.duplicated().any():
    raise RuntimeError("Duplicate OBJECTID values found in extraction")
if len(raw) != count:
    raise RuntimeError("The number of fetched records does not match the server count")

# Convert ArcGIS millisecond timestamps to DC-local dates for auditable CSV exports.
for col in ["ApplicationDate", "IntakeDate", "ReadyForReviewDate", "LastApprovalDate", "IssueDate"]:
    converted = pd.to_datetime(raw[col], unit="ms", utc=True, errors="coerce")
    raw[col] = converted.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M:%S")

raw.to_csv(OUT / "fy26_new_permits_candidates.csv", index=False)

summary = {
    "source": BASE,
    "filter_sql": WHERE,
    "extraction_date_utc": pd.Timestamp.now(tz="UTC").isoformat(),
    "record_count": len(raw),
    "unique_permit_numbers": int(raw.PermitNumber.nunique(dropna=True)),
    "duplicate_permit_number_rows": int(raw.PermitNumber.duplicated(keep=False).sum()),
    "missing_application_dates": int(raw.ApplicationDate.isna().sum()),
    "missing_approval_dates": int(raw.LastApprovalDate.isna().sum()),
    "missing_issue_dates": int(raw.IssueDate.isna().sum()),
    "status_counts": {str(k): int(v) for k,v in raw.StatusDescription.fillna("<MISSING>").value_counts().items()},
    "permit_type_description_counts": {str(k): int(v) for k,v in raw.PermitTypeDescription.fillna("<MISSING>").value_counts().items()},
    "top_type_detail_values": {str(k): int(v) for k,v in raw.TypeDetailNames.fillna("<MISSING>").value_counts().head(30).items()},
    "warning": "Preliminary extract, not the final approval-duration population. Date-field interpretations and business-day calculation require verification."
}
(OUT / "audit_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
print("\nStatus distribution:")
print(raw.StatusDescription.fillna("<MISSING>").value_counts().to_string())
print("\nTop construction activity descriptions:")
print(raw.TypeDetailNames.fillna("<MISSING>").value_counts().head(15).to_string())
print(f"\nDone. Outputs in: {OUT}")
