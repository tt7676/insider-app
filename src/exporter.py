"""Export processed transactions to CSV."""

import csv
from typing import List, Dict, Any


# Column order for CSV output
COLUMN_ORDER = [
    # Row identification
    'rowType',
    'eventId',
    'linkRole',

    # Roll-up aggregate fields
    'aggregateType',
    'aggregateShares',
    'aggregateValue',
    'priceRangeMin',
    'priceRangeMax',
    'tradeDateStart',
    'tradeDateEnd',
    'exerciseSharesEst',
    'exerciseSharesMethod',
    'soldNonTaxSum',
    'matchDelta',
    'matchStatus',
    'toleranceUsed',
    'hasTaxRows',
    'linkedTxnCount',

    # Issuer info
    'issuerCik',
    'issuerName',
    'issuerTradingSymbol',

    # Owner info
    'rptOwnerCik',
    'rptOwnerName',
    'officerTitle',

    # Transaction details
    'transactionDate',
    'securityTitle',
    'transactionCode',
    'transactionShares',
    'signedShares',
    'transactionPricePerShare',
    'transactionValue',
    'transactionAcquiredDisposedCode',
    'sharesOwnedFollowingTransaction',
    'directOrIndirectOwnership',

    # Calculated flags
    'label',
    'is10b5_1',
    'isTax',
    'taxType',
    'planAdoptionDate',
    'secTable',

    # Filing info
    'accessionNumber',
    'filedDate',
    'filingUrl',
    'footnote',
]


def to_csv(
    transactions: List[Dict[str, Any]],
    filepath: str,
    include_all_columns: bool = True
) -> None:
    """Export transactions to CSV file.

    Args:
        transactions: List of transaction dicts
        filepath: Output file path
        include_all_columns: If True, include columns not in COLUMN_ORDER
    """
    if not transactions:
        print("No transactions to export")
        return

    # Sort transactions: newest filedDate first, grouped by accessionNumber
    def sort_key(txn):
        filed_date = str(txn.get('filedDate', '') or '')
        accession = str(txn.get('accessionNumber', '') or '')
        return (filed_date, accession)

    transactions = sorted(transactions, key=sort_key, reverse=True)

    # Determine columns
    if include_all_columns:
        # Start with ordered columns, add any extras
        all_keys = set()
        for txn in transactions:
            all_keys.update(txn.keys())

        columns = [c for c in COLUMN_ORDER if c in all_keys]
        extra_columns = sorted(all_keys - set(columns))
        columns.extend(extra_columns)
    else:
        columns = COLUMN_ORDER

    # Write CSV
    with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()

        for txn in transactions:
            # Convert lists to strings (e.g., footnotes)
            row = {}
            for key, value in txn.items():
                if isinstance(value, list):
                    row[key] = ' | '.join(str(v) for v in value)
                elif isinstance(value, bool):
                    row[key] = str(value)
                else:
                    row[key] = value
            writer.writerow(row)

    print(f"Exported {len(transactions)} rows to {filepath}")


def to_dict_list(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return transactions as a list of dicts (for JSON or further processing).

    Args:
        transactions: List of transaction dicts

    Returns:
        Same list, cleaned for JSON serialization
    """
    result = []
    for txn in transactions:
        row = {}
        for key, value in txn.items():
            # Convert non-serializable types
            if value is None:
                row[key] = None
            elif isinstance(value, (int, float, str, bool, list)):
                row[key] = value
            else:
                row[key] = str(value)
        result.append(row)
    return result
