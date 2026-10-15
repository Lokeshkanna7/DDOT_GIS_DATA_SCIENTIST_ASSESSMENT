"""Extract DDOT TOPS occupancy permit location records for the assessment.

Run on Windows: python extract_permits.py
Requires: requests, pandas
"""

import logging
import time
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = (
    "https://maps2.dcgis.dc.gov/dcgis/rest/services/"
    "DCGIS_APPS/DDOT_TOPS/MapServer/24/query"
)
PAGE_SIZE = 2000
BASELINE_COUNT = 4926  # Prior discovery query; live data may change.
OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "permits_extracted.csv"

# Preserve the Phase 1 rule: application OR issue date on/after January 1, 2026.
# Note: this is an open-ended date filter, not an explicit calendar-year-only filter.
DATE_AND_TYPE = (
    "(ApplicationDate >= DATE '2026-01-01' "
    "OR IssueDate >= DATE '2026-01-01') "
    "AND EventTypeDescription IN "
    "('Construction Staging Area', 'Mobile Crane Work Zone')"
)

UTILITY_TERMS = [
    ("OwnerName", "PEPCO"),
    ("PermitteeName", "PEPCO"),
    ("ApplicantCompany", "PEPCO"),
    ("ApplicantCompany", "POTOMAC ELECTRIC POWER"),
    ("OwnerName", "DC WATER"),
    ("PermitteeName", "DC WATER"),
    ("ApplicantCompany", "DC WATER"),
    ("ApplicantCompany", "DCWATER"),
    ("OwnerName", "WASHINGTON GAS"),
    ("PermitteeName", "WASHINGTON GAS"),
    ("ApplicantCompany", "WASHINGTON GAS"),
    ("ApplicantCompany", "WASH GAS"),
]

UTILITY_CONDITION = " OR ".join(
    f"UPPER({column}) LIKE '%{term}%'" for column, term in UTILITY_TERMS
)
WHERE_CLAUSE = f"{DATE_AND_TYPE} AND ({UTILITY_CONDITION})"

DATE_COLUMNS = ("ApplicationDate", "IssueDate", "EffectiveDate", "ExpirationDate")


def make_session():
    """Reuse HTTP connections and retry transient server/network failures."""
    session = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def query_api(session, parameters):
    """Send a request, validate HTTP and ArcGIS-specific errors, return JSON."""
    response = session.get(API_URL, params=parameters, timeout=60)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("API did not return valid JSON") from exc
    if "error" in data:
        raise RuntimeError(f"ArcGIS API error: {data['error']}")
    return data


def get_expected_count(session):
    data = query_api(session, {
        "where": WHERE_CLAUSE,
        "returnCountOnly": "true",
        "returnExtentOnly": "false",
        "f": "json",
    })
    if "count" not in data:
        raise RuntimeError(f"Expected a count response; got keys {list(data)}")
    return int(data["count"])


def fetch_all_features(session, expected_count):
    """Fetch all records in ordered pages without deleting location-level rows."""
    features = []
    offset = 0
    while offset < expected_count:
        data = query_api(session, {
            "where": WHERE_CLAUSE,
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": 4326,  # Get geometry in longitude/latitude degrees.
            "orderByFields": "OBJECTID ASC",
            "resultOffset": offset,
            "resultRecordCount": PAGE_SIZE,
            "returnCountOnly": "false",
            "returnExtentOnly": "false",
            "f": "json",
        })
        page = data.get("features")
        if not isinstance(page, list) or not page:
            raise RuntimeError(
                f"No records returned at offset {offset}, "
                "despite expecting more. Source may have changed."
            )
        features.extend(page)
        logging.info("Page at offset %s: received %s (total %s/%s)",
                     offset, len(page), len(features), expected_count)
        offset += len(page)
        if len(features) > expected_count:
            raise RuntimeError("Retrieved more rows than the initial count. Rerun extraction.")
        # No fixed sleep is required; small courtesy delay between requests.
        time.sleep(0.2)
    return features


def features_to_dataframe(features):
    rows = []
    for feature in features:
        row = dict(feature.get("attributes") or {})
        geometry = feature.get("geometry") or {}
        row["GeometryLongitude"] = geometry.get("x")
        row["GeometryLatitude"] = geometry.get("y")
        rows.append(row)
    df = pd.DataFrame(rows)
    for column in DATE_COLUMNS:
        if column in df.columns:
            # ArcGIS dates arrive as Unix milliseconds. Keep UTC to avoid hidden
            # timezone assumptions; calendar dates in the filter are server-side.
            df[column] = pd.to_datetime(
                df[column], unit="ms", utc=True, errors="coerce"
            ).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return df


def validate(df, expected_count):
    if len(df) != expected_count:
        raise RuntimeError(
            f"Count mismatch: expected {expected_count}, downloaded {len(df)}."
        )
    if "OBJECTID" not in df or "TrackingNumber" not in df:
        raise RuntimeError("Required identifier fields missing from response.")
    duplicates = int(df["OBJECTID"].duplicated().sum())
    if duplicates:
        raise RuntimeError(f"Found {duplicates} duplicate OBJECTIDs: paging may overlap.")
    logging.info("Unique tracking numbers: %s", df["TrackingNumber"].nunique())
    for column in ("PermitNumber", "TrackingNumber", "LocationID",
                   "Longitude", "Latitude", "GeometryLongitude", "GeometryLatitude"):
        if column in df:
            logging.info("Missing %s: %s", column, int(df[column].isna().sum()))
    if {"TrackingNumber", "LocationID"}.issubset(df.columns):
        repeated = int(df.duplicated(["TrackingNumber", "LocationID"]).sum())
        logging.info("Repeated tracking/location pairs: %s (review if nonzero)", repeated)
    if expected_count != BASELINE_COUNT:
        logging.warning(
            "Current count %s differs from Phase 1 baseline %s. "
            "Check source updates and SQL rules.", expected_count, BASELINE_COUNT
        )


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    logging.info("Requesting DDOT count...")
    with make_session() as session:
        expected_count = get_expected_count(session)
        logging.info("API reports %s matching location records", expected_count)
        if expected_count == 0:
            raise RuntimeError("Query matched zero records; verify filters.")
        features = fetch_all_features(session, expected_count)
    df = features_to_dataframe(features)
    validate(df, expected_count)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    logging.info("Saved %s rows to %s", len(df), OUTPUT_PATH)


if __name__ == "__main__":
    main()
