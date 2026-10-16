"""Classify DDOT FY26 permit records into reproducible activity categories.

Inputs: ../DDOT_Tableau_Step2/fy26_permits_business_days_preliminary.csv
Outputs: tableau_permit_analysis_preliminary.csv, category_summary.csv, month_summary.csv,
         activity_category_audit.json

This remains PRELIMINARY until a sample of permit histories and date endpoints is verified.
"""
from pathlib import Path
import collections
import json
import pandas as pd

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'DDOT_Tableau_Step2' / 'fy26_permits_business_days_preliminary.csv'
if not SOURCE.exists():
    SOURCE = HERE / 'fy26_permits_business_days_preliminary.csv'
if not SOURCE.exists():
    raise FileNotFoundError('Place fy26_permits_business_days_preliminary.csv in Step4 or the sibling DDOT_Tableau_Step2 folder.')

df = pd.read_csv(SOURCE, dtype={'TrackingNumber': 'string', 'PermitNumber': 'string'})
if df['TrackingNumber'].isna().any() or df['TrackingNumber'].duplicated().any():
    raise ValueError('Input must be one record per nonblank TrackingNumber.')

KNOWN = {
    'Excavation', 'Paving', 'Fixture', 'Over Head Work', 'Projections',
    'Landscaping', 'Mega Projects', 'PS Rental'
}

def classify(value):
    # The source stores activities as comma-separated "Category; Description" items.
    prefixes = set()
    for part in str(value or '').split(','):
        if ';' in part:
            prefix = part.split(';', 1)[0].strip()
            if prefix:
                prefixes.add(prefix)
    if not prefixes:
        return pd.Series(['Unclassified', '', 0, 'Unclassified'])
    canonical = ', '.join(sorted(prefixes))
    unknown = prefixes - KNOWN
    if unknown:
        category = 'Needs review'
    elif len(prefixes) > 1:
        category = 'Multiple activity categories'
    else:
        category = next(iter(prefixes))
    return pd.Series([category, canonical, len(prefixes), 'Known' if not unknown else 'Unknown prefix'])

df[['ActivityCategory', 'ActivityCategoriesAll', 'ActivityCategoryCount', 'CategoryMappingStatus']] = (
    df['TypeDetailNames'].apply(classify)
)
df['MultiActivityFlag'] = df['ActivityCategoryCount'] > 1
# Make dashboard month labels stable and readily usable as actual dates in Tableau.
df['ApplicationMonthStart'] = pd.to_datetime(df['ApplicationDate'], errors='coerce').dt.to_period('M').dt.to_timestamp().dt.strftime('%Y-%m-%d')

def summary(key):
    return (df.groupby(key, dropna=False)
            .agg(PermitCount=('TrackingNumber','nunique'),
                 AvgApprovalBusinessDays=('ApprovalBusinessDays','mean'),
                 MedianApprovalBusinessDays=('ApprovalBusinessDays','median'),
                 Over90BusinessDays=('ApprovalOver90BusinessDays','sum'))
            .reset_index())

category_summary = summary('ActivityCategory').sort_values('PermitCount', ascending=False)
month_summary = summary('ApplicationMonthStart').sort_values('ApplicationMonthStart')
assert category_summary['PermitCount'].sum() == len(df)
assert month_summary['PermitCount'].sum() == len(df)

df.to_csv(HERE / 'tableau_permit_analysis_preliminary.csv', index=False)
category_summary.to_csv(HERE / 'category_summary.csv', index=False)
month_summary.to_csv(HERE / 'month_summary.csv', index=False)

audit = {
    'input_permits': len(df),
    'unique_tracking_numbers': int(df['TrackingNumber'].nunique()),
    'activity_categories': df['ActivityCategory'].value_counts().to_dict(),
    'multi_activity_permits': int(df['MultiActivityFlag'].sum()),
    'unmapped_prefixes': sorted({p for s in df.loc[df.CategoryMappingStatus == 'Unknown prefix', 'ActivityCategoriesAll'] for p in s.split(', ')}),
    'overall_average_preliminary_business_days': float(df['ApprovalBusinessDays'].mean()),
    'warnings': [
        'Approval end date (LastApprovalDate) and same-day counting convention require confirmation from sample DDOT permit histories.',
        'Category is derived from all semicolon-delimited prefixes in TypeDetailNames. Multi-category permits are assigned to one separate group to prevent double-counting.',
        'FY26 eligibility is based on ApplicationDate, IssueDate and PermitType=0; verify that new permit also meets DDOT definition for renewals/reissues.',
        'As of extraction, statuses may differ from status at issuance.',
        'The FY26 population includes permits extracted on 2026-10-14 and may change with source refreshes.'
    ]
}
(HERE / 'activity_category_audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
print('Classified', len(df), 'unique permits')
print(category_summary.to_string(index=False, formatters={'AvgApprovalBusinessDays':'{:.2f}'.format}))
print('Outputs saved to:', HERE)
