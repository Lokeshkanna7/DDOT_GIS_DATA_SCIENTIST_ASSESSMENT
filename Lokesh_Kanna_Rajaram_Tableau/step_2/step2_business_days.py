"""DDOT Tableau FY26 step 2: calculate preliminary approval business days.
Run in directory containing fy26_permit_level_preliminary.csv, or beside this script.
Policy: exclude application date; include approval date when it is an eligible workday.
Assumes one standard Monday-Friday DC-government holiday schedule, not agency-specific closures.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / 'fy26_permit_level_preliminary.csv'
OUTPUT = ROOT / 'fy26_permits_business_days_preliminary.csv'
AUDIT = ROOT / 'step2_business_day_audit.json'

# Official DC government published Monday-Friday holidays within FY26.
# https://edpm.dc.gov/issuances/legal-public-holidays-2025/
# https://edpm.dc.gov/issuances/legal-public-holidays/
HOLIDAYS = {
    '2025-10-13': 'Indigenous Peoples Day',
    '2025-11-11': 'Veterans Day',
    '2025-11-27': 'Thanksgiving Day',
    '2025-12-25': 'Christmas Day',
    '2026-01-01': 'New Years Day',
    '2026-01-19': 'Martin Luther King Jr. Day',
    '2026-02-16': 'Washingtons Birthday',
    '2026-04-16': 'DC Emancipation Day',
    '2026-05-25': 'Memorial Day',
    '2026-06-19': 'Juneteenth',
    '2026-07-03': 'Independence Day observed',
    '2026-09-07': 'Labor Day',
}


def main():
    data = pd.read_csv(INPUT, low_memory=False)
    for c in ['ApplicationDate', 'LastApprovalDate', 'IssueDate']:
        data[c] = pd.to_datetime(data[c], errors='coerce')
    start = data['ApplicationDate']
    end = data['LastApprovalDate']
    invalid = start.isna() | end.isna() | end.lt(start)
    valid = ~invalid
    vals = pd.Series(pd.NA, index=data.index, dtype='Int64')
    calendar = np.busdaycalendar(weekmask='1111100', holidays=np.array(list(HOLIDAYS), dtype='datetime64[D]'))
    s = start[valid].to_numpy(dtype='datetime64[D]')
    e = end[valid].to_numpy(dtype='datetime64[D]')
    # Count workdays in (application_date, approval_date], so same day = 0.
    result = np.busday_count(s + np.timedelta64(1, 'D'), e + np.timedelta64(1, 'D'), busdaycal=calendar)
    vals.loc[valid] = result
    data['ApprovalBusinessDays'] = vals
    data['ApprovalDateValid'] = valid
    data['ApplicationMonth'] = start.dt.strftime('%Y-%m')
    data['ApprovalMonth'] = end.dt.strftime('%Y-%m')
    data['ApprovalCalendarDays'] = (end.dt.normalize() - start.dt.normalize()).dt.days
    existing = pd.to_numeric(data['DaysToApprove'], errors='coerce')
    data['ExistingMinusCalculatedBusinessDays'] = existing - data['ApprovalBusinessDays'].astype('Float64')
    data['ApprovalOver90BusinessDays'] = data['ApprovalBusinessDays'].gt(90)
    data.to_csv(OUTPUT, index=False, date_format='%Y-%m-%d %H:%M:%S')
    audit = {
        'rows': len(data),
        'unique_tracking_numbers': int(data['TrackingNumber'].nunique()),
        'invalid_approval_dates': int(invalid.sum()),
        'business_day_policy': 'Exclude application calendar date; include approval date if weekday and not DC holiday; same-day equals zero',
        'holiday_dates': HOLIDAYS,
        'mean_business_days': float(data['ApprovalBusinessDays'].mean()),
        'median_business_days': float(data['ApprovalBusinessDays'].median()),
        'p90_business_days': float(data['ApprovalBusinessDays'].quantile(.90)),
        'over_90_business_days_count': int(data['ApprovalOver90BusinessDays'].sum()),
        'existing_days_to_approve_exact_match_count': int(data['ExistingMinusCalculatedBusinessDays'].eq(0).sum()),
        'existing_days_to_approve_disagree_count': int(data['ExistingMinusCalculatedBusinessDays'].ne(0).sum()),
        'status_counts': data['StatusDescription'].value_counts(dropna=False).to_dict(),
        'warning': 'Preliminary computed metric; validate business endpoint and counting convention with DDOT permit examples prior to final dashboard.'
    }
    AUDIT.write_text(json.dumps(audit, indent=2, default=str), encoding='utf8')
    print('Saved', OUTPUT)
    print('Saved', AUDIT)
    print('Rows:',audit['rows'],'Mean:',round(audit['mean_business_days'],2),'Median:',audit['median_business_days'])
    print('Existing source days-to-approve disagreements:',audit['existing_days_to_approve_disagree_count'])

if __name__ == '__main__':
    main()
