"""Form 4 Insider Transaction Tool - Main Entry Point.

Usage:
    python main.py --cik 1765417 --issuer-cik 1512673
    python main.py --cik 1765417 --issuer-cik 1512673 --output custom.csv
    python main.py --cik 1765417 --issuer-cik 1512673 --max-filings 10
"""

import argparse
import sys
from datetime import datetime
from typing import Optional

from src.datamule_client import get_all_filings_data
from src.transaction_parser import parse_filing
from src.rollup_builder import build_rollups
from src.exporter import to_csv


def generate_filename(transactions: list) -> str:
    """Generate output filename from transaction data.

    Format: {companyName}_{insiderName}_{DD.MM.YY}.csv
    Example: BlockInc_AhujaAmrita_09.01.26.csv
    """
    if not transactions:
        return 'output.csv'
    t = transactions[0]
    company = t.get('issuerName', 'Unknown').replace(',', '').replace(' ', '')
    insider = t.get('rptOwnerName', 'Unknown').replace(',', '').replace(' ', '')
    date = datetime.now().strftime('%d.%m.%y')
    return f"{company}_{insider}_{date}.csv"


def process_insider(
    cik: int,
    issuer_cik: int,
    output_file: Optional[str] = None,
    max_filings: Optional[int] = None,
    skip_rollups: bool = False,
    verbose: bool = True
) -> int:
    """Process all Form 4 filings for an insider at a specific company.

    Args:
        cik: The CIK of the reporting owner (insider)
        issuer_cik: The CIK of the issuer (company) to filter by
        output_file: Path to output CSV file (auto-generated if None)
        max_filings: Maximum filings to process (None = all)
        skip_rollups: If True, skip roll-up aggregation
        verbose: Print progress

    Returns:
        Number of transactions processed
    """
    # Step 1: Fetch filings from Datamule
    filings = get_all_filings_data(
        rpt_owner_cik=cik,
        max_filings=max_filings,
        verbose=verbose
    )

    if not filings:
        print(f"No filings found for CIK {cik}")
        return 0

    # Step 2: Parse each filing
    all_transactions = []
    for filing in filings:
        transactions = parse_filing(filing)
        all_transactions.extend(transactions)

    if verbose:
        print(f"Parsed {len(all_transactions)} transactions from {len(filings)} filings")

    # Step 3: Filter by issuer CIK (company)
    # CIKs in SEC data have leading zeros (e.g., '0001512673'), normalize by converting to int
    all_transactions = [
        t for t in all_transactions
        if int(t.get('issuerCik', '0') or '0') == issuer_cik
    ]

    if not all_transactions:
        print(f"No transactions found for issuer CIK {issuer_cik}")
        return 0

    if verbose:
        company_name = all_transactions[0].get('issuerName', 'Unknown')
        print(f"Filtered to {len(all_transactions)} transactions for {company_name} (CIK {issuer_cik})")

    # Step 5: Build roll-ups (optional)
    if not skip_rollups:
        if verbose:
            print("Building roll-ups...")

        # Group transactions by filing for roll-up processing
        filings_transactions = {}
        for txn in all_transactions:
            acc = txn.get('accessionNumber', '')
            filings_transactions.setdefault(acc, []).append(txn)

        # Process roll-ups per filing
        processed_transactions = []
        for acc, txns in filings_transactions.items():
            rolled = build_rollups(txns)
            processed_transactions.extend(rolled)

        all_transactions = processed_transactions

        if verbose:
            rollup_count = sum(1 for t in all_transactions if t.get('rowType') == 'ROLLUP')
            print(f"Created {rollup_count} roll-up summaries")

    # Step 6: Export to CSV
    if output_file is None:
        output_file = generate_filename(all_transactions)
    to_csv(all_transactions, output_file)

    return len(all_transactions)


def main():
    parser = argparse.ArgumentParser(
        description='Process Form 4 insider transactions using Datamule'
    )
    parser.add_argument(
        '--cik',
        type=int,
        required=True,
        help='CIK of the reporting owner (insider)'
    )
    parser.add_argument(
        '--issuer-cik',
        type=int,
        required=True,
        help='CIK of the issuer (company) to filter by'
    )
    parser.add_argument(
        '--output', '-o',
        type=str,
        default=None,
        help='Output CSV file path (auto-generated if not specified)'
    )
    parser.add_argument(
        '--max-filings',
        type=int,
        default=None,
        help='Maximum number of filings to process (default: all)'
    )
    parser.add_argument(
        '--skip-rollups',
        action='store_true',
        help='Skip roll-up aggregation'
    )
    parser.add_argument(
        '--quiet', '-q',
        action='store_true',
        help='Suppress progress output'
    )

    args = parser.parse_args()

    try:
        count = process_insider(
            cik=args.cik,
            issuer_cik=args.issuer_cik,
            output_file=args.output,
            max_filings=args.max_filings,
            skip_rollups=args.skip_rollups,
            verbose=not args.quiet
        )
        print(f"\nDone! Processed {count} transaction rows.")
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
