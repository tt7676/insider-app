"""Datamule client for fetching insider transaction filings.

Two-step workflow:
1. Paid API -> Get list of accession numbers for an insider
2. Submission tool -> Download and parse each filing with full data
"""

import os
from typing import List, Optional, Dict, Any

import requests
from dotenv import load_dotenv
from datamule import Submission, format_accession


load_dotenv()


def get_api_key() -> str:
    """Get Datamule API key from environment."""
    key = os.environ.get('DATAMULE_API_KEY')
    if not key:
        raise ValueError("DATAMULE_API_KEY not found in environment")
    return key


def get_filings_for_insider(
    rpt_owner_cik: int,
    page_size: int = 100,
    max_pages: Optional[int] = None
) -> List[str]:
    """Get all accession numbers for an insider using paid API.

    Args:
        rpt_owner_cik: The CIK of the reporting owner (insider)
        page_size: Number of results per page (max 100)
        max_pages: Maximum pages to fetch (None = all pages)

    Returns:
        List of accession numbers (deduplicated)
    """
    api_key = get_api_key()
    all_accessions = []
    page = 1

    while True:
        response = requests.get(
            'https://api.datamule.xyz/insider-transactions',
            params={
                'table': 'reporting-owner',
                'rptOwnerCik': rpt_owner_cik,
                'page': page,
                'pageSize': page_size,
                'api_key': api_key
            },
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        records = data.get('data', [])

        if not records:
            break

        all_accessions.extend([r['accessionNumber'] for r in records])

        # Check pagination
        total_pages = data.get('pagination', {}).get('totalPages', 1)
        if page >= total_pages:
            break
        if max_pages and page >= max_pages:
            break

        page += 1

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for acc in all_accessions:
        if acc not in seen:
            seen.add(acc)
            unique.append(acc)

    return unique


def get_filing_data(accession_number: str) -> Optional[Dict[str, Any]]:
    """Download and parse a single filing using Submission tool.

    Args:
        accession_number: The accession number (any format)

    Returns:
        The doc.data dict for the Form 4, or None if not found
    """
    # Format accession for URL (18-digit, no dashes)
    acc_formatted = format_accession(str(accession_number), 'no-dash')
    url = f'https://sec-library.datamule.xyz/{acc_formatted}.sgml'

    try:
        sub = Submission(url=url)

        for doc in sub:
            if doc.type in ['3', '4', '5', '3/A', '4/A', '5/A']:
                return {
                    'accessionNumber': accession_number,
                    'documentType': doc.type,
                    'data': doc.data,
                    'filedDate': str(sub.filing_date) if sub.filing_date else None
                }

        return None

    except Exception as e:
        print(f"Error fetching {accession_number}: {e}")
        return None


def get_all_filings_data(
    rpt_owner_cik: int,
    max_filings: Optional[int] = None,
    verbose: bool = True
) -> List[Dict[str, Any]]:
    """Get full data for all filings for an insider.

    Args:
        rpt_owner_cik: The CIK of the reporting owner (insider)
        max_filings: Maximum filings to fetch (None = all)
        verbose: Print progress

    Returns:
        List of filing data dicts
    """
    # Step 1: Get accession numbers
    if verbose:
        print(f"Fetching accession numbers for CIK {rpt_owner_cik}...")

    accessions = get_filings_for_insider(rpt_owner_cik)

    if max_filings:
        accessions = accessions[:max_filings]

    if verbose:
        print(f"Found {len(accessions)} filings")

    # Step 2: Download each filing
    filings = []
    for i, acc in enumerate(accessions):
        if verbose:
            print(f"  [{i+1}/{len(accessions)}] Fetching {acc}...")

        filing_data = get_filing_data(acc)
        if filing_data:
            filings.append(filing_data)

    if verbose:
        print(f"Successfully fetched {len(filings)} filings")

    return filings
