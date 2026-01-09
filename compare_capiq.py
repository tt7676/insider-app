"""Compare our Form 4 output against CAPIQ data to find discrepancies."""

import argparse
import re
from datetime import datetime
from typing import Dict, List, Tuple
from collections import defaultdict

import pandas as pd


def parse_capiq_shares(value) -> float:
    """Parse CAPIQ share value, handling parentheses for negatives."""
    if pd.isna(value):
        return 0.0
    s = str(value).replace(',', '').strip()
    if s.startswith('(') and s.endswith(')'):
        return -float(s[1:-1])
    return float(s)


def parse_capiq_date(value) -> str:
    """Parse CAPIQ date to YYYY-MM-DD format."""
    if pd.isna(value):
        return ''
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    # Try parsing string formats
    s = str(value).strip()
    for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y']:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return s


def load_capiq_data(filepath: str) -> pd.DataFrame:
    """Load CAPIQ xlsx file with correct header row."""
    # CAPIQ files have metadata in first ~54 rows
    df = pd.read_excel(filepath, header=None)

    # Find header row by looking for 'Holder Name'
    header_row = None
    for i in range(min(100, len(df))):
        row = df.iloc[i].tolist()
        if 'Holder Name' in str(row):
            header_row = i
            break

    if header_row is None:
        raise ValueError("Could not find 'Holder Name' header in CAPIQ file")

    # Re-read with correct header
    df = pd.read_excel(filepath, header=header_row)

    # Parse dates and shares
    df['trade_date'] = df['Trade Date Range'].apply(parse_capiq_date)
    df['filed_date'] = df['Filed Date'].apply(parse_capiq_date)
    df['shares'] = df['Transacted Shares'].apply(parse_capiq_shares)

    return df


def load_our_data(filepath: str) -> pd.DataFrame:
    """Load our CSV output."""
    df = pd.read_csv(filepath, encoding='utf-8-sig')

    # Filter out ROLLUP rows - only compare SOURCE and SYNTHETIC
    df = df[df['rowType'] != 'ROLLUP'].copy()

    # No Table 2 filter - CAPIQ shows both Table 1 and Table 2 rows
    # (acquisitions AND disposals including option exercises)

    # Normalize date format
    df['trade_date'] = df['transactionDate'].astype(str).str[:10]
    df['filed_date'] = df['filedDate'].astype(str).str[:10]
    # Use signedShares which has correct +/- sign
    df['shares'] = df['signedShares'].fillna(0).astype(float)

    return df


def group_by_filing(df: pd.DataFrame) -> Dict[Tuple[str, str], List[float]]:
    """Group share amounts by (trade_date, filed_date)."""
    groups = defaultdict(list)
    for _, row in df.iterrows():
        key = (row['trade_date'], row['filed_date'])
        shares = row['shares']
        if shares != 0:
            groups[key].append(shares)
    return groups


def is_likely_rollup(value: float, all_values: List[float]) -> bool:
    """Check if a value is likely a rollup (sum of other values)."""
    # Check if this value equals the sum of some other values
    other_values = [v for v in all_values if v != value]
    if len(other_values) < 2:
        return False

    # Check common rollup patterns: sum of 2-4 values
    from itertools import combinations
    for r in range(2, min(5, len(other_values) + 1)):
        for combo in combinations(other_values, r):
            if abs(sum(combo) - value) < 0.01:  # Float tolerance
                return True
    return False


def compare_share_lists(capiq_shares: List[float], our_shares: List[float]) -> Tuple[bool, List[float], List[float], List[float]]:
    """Compare two lists of share amounts.

    Returns:
        (is_match, missing_in_ours, extra_in_ours, likely_capiq_rollups)
    """
    capiq_sorted = sorted(capiq_shares)
    our_sorted = sorted(our_shares)

    if capiq_sorted == our_sorted:
        return True, [], [], []

    # Find differences
    capiq_remaining = capiq_sorted.copy()
    our_remaining = our_sorted.copy()

    for share in capiq_sorted:
        if share in our_remaining:
            our_remaining.remove(share)
            capiq_remaining.remove(share)

    # Check if missing values are likely CAPIQ rollups
    likely_rollups = []
    for missing in capiq_remaining:
        if is_likely_rollup(missing, capiq_shares):
            likely_rollups.append(missing)

    return False, capiq_remaining, our_remaining, likely_rollups


def categorize_mismatch(m: Dict) -> str:
    """Categorize a mismatch into a bucket type."""
    missing = m.get('missing', [])
    extra = m.get('extra', [])
    likely_rollups = m.get('likely_rollups', [])
    capiq_shares = m.get('capiq_shares', [])

    # Check for duplicate value pattern (same value appears twice in CAPIQ)
    has_duplicate = False
    for val in missing:
        if capiq_shares.count(val) > 1:
            has_duplicate = True
            break

    # Categorize
    if has_duplicate and not extra:
        return 'duplicate_pattern'
    elif likely_rollups and set(likely_rollups) == set(missing):
        return 'capiq_rollup'
    elif likely_rollups:
        return 'partial_rollup'
    elif extra and missing:
        return 'data_difference'
    elif missing and not extra:
        return 'missing_in_ours'
    elif extra and not missing:
        return 'extra_in_ours'
    else:
        return 'other'


def generate_report(
    capiq_groups: Dict[Tuple[str, str], List[float]],
    our_groups: Dict[Tuple[str, str], List[float]],
    insider_name: str
) -> str:
    """Generate comparison report with categorized mismatches."""
    lines = []

    # Get all filing keys
    all_keys = set(capiq_groups.keys()) | set(our_groups.keys())

    matches = 0
    mismatches = []
    missing_in_ours_filings = []
    missing_in_capiq_filings = []

    for key in sorted(all_keys):
        trade_date, filed_date = key
        capiq_shares = capiq_groups.get(key, [])
        our_shares = our_groups.get(key, [])

        if not capiq_shares:
            missing_in_capiq_filings.append(key)
            continue
        if not our_shares:
            missing_in_ours_filings.append(key)
            continue

        is_match, missing, extra, likely_rollups = compare_share_lists(capiq_shares, our_shares)

        if is_match:
            matches += 1
        else:
            mismatch = {
                'trade_date': trade_date,
                'filed_date': filed_date,
                'capiq_shares': capiq_shares,
                'our_shares': our_shares,
                'missing': missing,
                'extra': extra,
                'likely_rollups': likely_rollups
            }
            mismatch['category'] = categorize_mismatch(mismatch)
            mismatches.append(mismatch)

    # Group mismatches by category
    buckets = {
        'duplicate_pattern': [],
        'capiq_rollup': [],
        'partial_rollup': [],
        'data_difference': [],
        'missing_in_ours': [],
        'extra_in_ours': [],
        'other': []
    }
    for m in mismatches:
        buckets[m['category']].append(m)

    # Bucket descriptions
    bucket_info = {
        'duplicate_pattern': {
            'title': 'CAPIQ DUPLICATE VALUES (Non-Issue)',
            'desc': 'CAPIQ shows the same value twice (likely rollup + source row). Not a data problem.',
            'icon': '[~]'
        },
        'capiq_rollup': {
            'title': 'CAPIQ ROLLUPS AS SOURCE ROWS (Non-Issue)',
            'desc': 'CAPIQ includes rollup totals as regular rows. Our rollups are filtered out.',
            'icon': '[~]'
        },
        'partial_rollup': {
            'title': 'PARTIAL ROLLUP MATCHES (Review)',
            'desc': 'Some missing values are rollups, but there are other differences too.',
            'icon': '[?]'
        },
        'data_difference': {
            'title': 'DATA DIFFERENCES (Investigate)',
            'desc': 'Values differ between sources - may indicate parsing issue or data error.',
            'icon': '[!]'
        },
        'missing_in_ours': {
            'title': 'VALUES MISSING IN OUR DATA (Investigate)',
            'desc': 'CAPIQ has values we don\'t have - may need to check SEC filing.',
            'icon': '[!]'
        },
        'extra_in_ours': {
            'title': 'EXTRA VALUES IN OUR DATA (Review)',
            'desc': 'We have values CAPIQ doesn\'t show.',
            'icon': '[?]'
        },
        'other': {
            'title': 'OTHER MISMATCHES',
            'desc': 'Uncategorized differences.',
            'icon': '[?]'
        }
    }

    # Count non-issues vs issues to investigate
    non_issue_count = len(buckets['duplicate_pattern']) + len(buckets['capiq_rollup'])
    investigate_count = len(buckets['data_difference']) + len(buckets['missing_in_ours'])
    review_count = len(buckets['partial_rollup']) + len(buckets['extra_in_ours']) + len(buckets['other'])

    # Header
    lines.append("=" * 80)
    lines.append(f"CAPIQ COMPARISON REPORT - {insider_name}")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 80)
    lines.append("")

    # High-level summary
    total = matches + len(mismatches) + len(missing_in_ours_filings) + len(missing_in_capiq_filings)
    lines.append("SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total filings compared:    {total}")
    lines.append(f"  Perfect matches:           {matches}")
    lines.append(f"  Mismatches:                {len(mismatches)}")
    lines.append(f"  Missing in our data:       {len(missing_in_ours_filings)}")
    lines.append(f"  Missing in CAPIQ:          {len(missing_in_capiq_filings)}")
    lines.append("")

    # Mismatch breakdown
    lines.append("MISMATCH BREAKDOWN")
    lines.append("-" * 40)
    lines.append(f"  Non-Issues (presentation differences):")
    lines.append(f"    - Duplicate values:      {len(buckets['duplicate_pattern'])}")
    lines.append(f"    - CAPIQ rollups:         {len(buckets['capiq_rollup'])}")
    lines.append(f"    Subtotal:                {non_issue_count}")
    lines.append("")
    lines.append(f"  To Investigate:")
    lines.append(f"    - Data differences:      {len(buckets['data_difference'])}")
    lines.append(f"    - Missing in ours:       {len(buckets['missing_in_ours'])}")
    lines.append(f"    Subtotal:                {investigate_count}")
    lines.append("")
    lines.append(f"  To Review:")
    lines.append(f"    - Partial rollups:       {len(buckets['partial_rollup'])}")
    lines.append(f"    - Extra in ours:         {len(buckets['extra_in_ours'])}")
    lines.append(f"    - Other:                 {len(buckets['other'])}")
    lines.append(f"    Subtotal:                {review_count}")
    lines.append("")
    lines.append("=" * 80)

    # Helper to format a single mismatch
    def format_mismatch(m: Dict) -> List[str]:
        result = []
        result.append(f"  Filing: {m['trade_date']} (Filed: {m['filed_date']})")
        result.append(f"    CAPIQ:  {sorted(m['capiq_shares'])}")
        result.append(f"    Ours:   {sorted(m['our_shares'])}")
        if m['missing']:
            result.append(f"    Missing in ours: {m['missing']}")
        if m['extra']:
            result.append(f"    Extra in ours:   {m['extra']}")
        return result

    # Output each bucket with its mismatches
    bucket_order = ['data_difference', 'missing_in_ours', 'partial_rollup',
                    'extra_in_ours', 'duplicate_pattern', 'capiq_rollup', 'other']

    for bucket_key in bucket_order:
        bucket_mismatches = buckets[bucket_key]
        if not bucket_mismatches:
            continue

        info = bucket_info[bucket_key]
        lines.append("")
        lines.append(f"{info['icon']} {info['title']} ({len(bucket_mismatches)})")
        lines.append(f"   {info['desc']}")
        lines.append("-" * 60)

        for m in bucket_mismatches:
            lines.extend(format_mismatch(m))
            lines.append("")

    # Missing filings sections
    if missing_in_ours_filings:
        lines.append("")
        lines.append(f"[!] FILINGS MISSING IN OUR DATA ({len(missing_in_ours_filings)})")
        lines.append("   Filings that exist in CAPIQ but not in our output.")
        lines.append("-" * 60)
        for key in missing_in_ours_filings:
            lines.append(f"  Trade: {key[0]}, Filed: {key[1]}")

    if missing_in_capiq_filings:
        lines.append("")
        lines.append(f"[~] FILINGS MISSING IN CAPIQ ({len(missing_in_capiq_filings)})")
        lines.append("   Filings we have that CAPIQ doesn't show.")
        lines.append("-" * 60)
        for key in missing_in_capiq_filings:
            lines.append(f"  Trade: {key[0]}, Filed: {key[1]}")

    # Final summary
    lines.append("")
    lines.append("=" * 80)
    lines.append(f"RESULT: {matches} perfect matches, {investigate_count} to investigate, {non_issue_count} non-issues")
    lines.append("=" * 80)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Compare our Form 4 output against CAPIQ data')
    parser.add_argument('--capiq', required=True, help='Path to CAPIQ xlsx file')
    parser.add_argument('--ours', required=True, help='Path to our CSV file')
    parser.add_argument('--name', default='Unknown Insider', help='Insider name for report')
    parser.add_argument('--output', help='Output file path (optional, prints to console if not specified)')

    args = parser.parse_args()

    print(f"Loading CAPIQ data from {args.capiq}...")
    capiq_df = load_capiq_data(args.capiq)
    print(f"  Loaded {len(capiq_df)} rows")

    print(f"Loading our data from {args.ours}...")
    our_df = load_our_data(args.ours)
    print(f"  Loaded {len(our_df)} rows")

    print("Grouping by filing...")
    capiq_groups = group_by_filing(capiq_df)
    our_groups = group_by_filing(our_df)

    print(f"  CAPIQ: {len(capiq_groups)} filings")
    print(f"  Ours: {len(our_groups)} filings")

    print("Generating report...")
    report = generate_report(capiq_groups, our_groups, args.name)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"Report saved to {args.output}")
    else:
        # Write to temp file and print safe version
        with open('comparison_report.txt', 'w', encoding='utf-8') as f:
            f.write(report)
        # Print ASCII-safe version
        safe_report = report.replace('❌', '[X]').replace('✅', '[OK]').replace('⚠️', '[!]')
        print()
        print(safe_report)


if __name__ == '__main__':
    main()
