"""Test script to compare DB API vs Submission approach.

Goal: Determine if we can simplify to one-step workflow by checking
if the DB API provides all the fields we need:
- aff10b5One (10b5-1 plan indicator)
- Transaction code F (tax withholding)
- Footnotes
- Tax withholding info
- Price per share (actual values)
"""

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()


def get_api_key():
    key = os.environ.get('DATAMULE_API_KEY')
    if not key:
        raise ValueError("DATAMULE_API_KEY not found in environment")
    return key


def test_db_api_tables():
    """Test querying different DB tables to see available fields."""
    api_key = get_api_key()

    # Test with a known insider CIK (Amrita from previous tests)
    test_cik = 1765417

    tables = [
        'reporting-owner',
        'non-derivative-transaction',
        'non-derivative-holding',
        'derivative-transaction',
        'derivative-holding',
        'metadata',
    ]

    results = {}

    for table in tables:
        print(f"\n{'='*60}")
        print(f"Testing table: {table}")
        print('='*60)

        try:
            response = requests.get(
                'https://api.datamule.xyz/insider-transactions',
                params={
                    'table': table,
                    'rptOwnerCik': test_cik,
                    'page': 1,
                    'pageSize': 5,  # Just get a few for inspection
                    'api_key': api_key
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            records = data.get('data', [])
            pagination = data.get('pagination', {})

            print(f"Total records: {pagination.get('total', 'N/A')}")
            print(f"Sample records: {len(records)}")

            if records:
                # Show all fields in first record
                print("\nFields in first record:")
                first_record = records[0]
                for key, value in sorted(first_record.items()):
                    # Truncate long values
                    val_str = str(value)
                    if len(val_str) > 100:
                        val_str = val_str[:100] + "..."
                    print(f"  {key}: {val_str}")

                results[table] = {
                    'fields': list(first_record.keys()),
                    'sample': first_record,
                    'total': pagination.get('total', 0)
                }
            else:
                print("No records found")
                results[table] = {'fields': [], 'sample': None, 'total': 0}

        except Exception as e:
            print(f"Error: {e}")
            results[table] = {'error': str(e)}

    return results


def check_key_fields(results):
    """Check if the key fields we need are present in the DB tables."""
    print("\n" + "="*60)
    print("KEY FIELD ANALYSIS")
    print("="*60)

    key_fields = {
        'aff10b5One': '10b5-1 plan indicator',
        'transactionCode': 'Transaction type (P, S, A, F, etc.)',
        'footnote': 'Footnote text',
        'transactionPricePerShare': 'Price per share',
        'transactionShares': 'Number of shares',
        'transactionAcquiredDisposedCode': 'Acquire/Dispose indicator',
    }

    for field, description in key_fields.items():
        print(f"\n{field} ({description}):")
        found_in = []
        for table, data in results.items():
            if 'fields' in data and field in data['fields']:
                found_in.append(table)
                sample_val = data['sample'].get(field)
                print(f"  Found in {table}: {sample_val}")

        if not found_in:
            print("  NOT FOUND in any table!")


def compare_specific_transaction():
    """Compare a specific transaction between DB API and Submission."""
    from datamule import Submission, format_accession

    api_key = get_api_key()
    test_cik = 1765417

    print("\n" + "="*60)
    print("COMPARING DB API vs SUBMISSION APPROACH")
    print("="*60)

    # Get one accession number from reporting-owner table
    response = requests.get(
        'https://api.datamule.xyz/insider-transactions',
        params={
            'table': 'reporting-owner',
            'rptOwnerCik': test_cik,
            'page': 1,
            'pageSize': 1,
            'api_key': api_key
        },
        timeout=30
    )
    response.raise_for_status()
    reporting_data = response.json()['data'][0]
    accession = reporting_data['accessionNumber']

    print(f"\nTest accession: {accession}")

    # Get non-derivative transactions for this accession from DB
    print("\n--- FROM DB API (non-derivative-transaction table) ---")
    response = requests.get(
        'https://api.datamule.xyz/insider-transactions',
        params={
            'table': 'non-derivative-transaction',
            'accessionNumber': accession,
            'api_key': api_key
        },
        timeout=30
    )
    response.raise_for_status()
    db_transactions = response.json().get('data', [])

    print(f"Found {len(db_transactions)} transactions")
    if db_transactions:
        print("First transaction fields:")
        for k, v in sorted(db_transactions[0].items()):
            print(f"  {k}: {v}")

    # Get from metadata table
    print("\n--- FROM DB API (metadata table) ---")
    response = requests.get(
        'https://api.datamule.xyz/insider-transactions',
        params={
            'table': 'metadata',
            'accessionNumber': accession,
            'api_key': api_key
        },
        timeout=30
    )
    response.raise_for_status()
    metadata = response.json().get('data', [])

    if metadata:
        print("Metadata fields:")
        for k, v in sorted(metadata[0].items()):
            print(f"  {k}: {v}")

    # Get from Submission approach
    print("\n--- FROM SUBMISSION APPROACH ---")
    acc_formatted = format_accession(str(accession), 'no-dash')
    url = f'https://sec-library.datamule.xyz/{acc_formatted}.sgml'

    sub = Submission(url=url)
    for doc in sub:
        if doc.type in ['4', '4/A']:
            ownership_doc = doc.data.get('ownershipDocument', {})
            nd_table = ownership_doc.get('nonDerivativeTable', {})
            nd_transactions = nd_table.get('nonDerivativeTransaction', [])

            if isinstance(nd_transactions, dict):
                nd_transactions = [nd_transactions]

            print(f"Found {len(nd_transactions)} transactions")
            if nd_transactions:
                print("\nFirst transaction (full structure):")
                print(json.dumps(nd_transactions[0], indent=2, default=str))
            break


if __name__ == '__main__':
    print("Testing DB API approach vs two-step Submission approach")
    print("="*60)

    # Test all tables
    results = test_db_api_tables()

    # Check key fields
    check_key_fields(results)

    # Compare specific transaction
    compare_specific_transaction()
