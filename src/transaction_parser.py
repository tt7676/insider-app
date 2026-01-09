"""Parse Datamule filing data into transaction rows with calculated fields.

Takes doc.data from Datamule Submission and extracts:
- All transactions from non-derivative and derivative tables
- Adds calculated fields (transactionValue, signedShares, is10b5_1, etc.)
- Uses Datamule native field names
"""

import re
from typing import Dict, List, Any, Optional

from .classifier import classify, get_link_role


# Regex patterns for detection
RULE_10B5_1_RE = re.compile(r"10b5-?1", re.IGNORECASE)

TAX_RE = re.compile(
    r"(withhold|withholding|withheld|tax(es)?|sell-?\s?to-?\s?cover|"
    r"net\s+share\s+settle(ment)?|to\s+satisfy\s+(applicable\s+)?tax)",
    re.IGNORECASE
)

ISSUER_RE = re.compile(
    r"(to\s+the\s+issuer|to\s+issuer|surrendered\s+to\s+(the\s+)?(issuer|company)|"
    r"withheld\s+by\s+(the\s+)?issuer|tendered\s+to\s+(the\s+)?(issuer|company))",
    re.IGNORECASE
)

ADOPTION_RE = re.compile(
    r"(adopt(?:ed|ion)?|entered\s+into)",
    re.IGNORECASE
)

DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
DATE_MDY_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},\s*\d{4}\b",
    re.IGNORECASE
)


def _format_accession_with_dashes(accession: str) -> str:
    """Format accession number with dashes: 0001209191-21-038188"""
    acc = str(accession).replace('-', '').zfill(18)
    return f"{acc[:10]}-{acc[10:12]}-{acc[12:]}"


def _build_sec_filing_url(issuer_cik: str, accession: str) -> str:
    """Build SEC EDGAR filing URL.

    Example: https://www.sec.gov/Archives/edgar/data/1512673/000120919121038188/0001209191-21-038188-index.htm
    """
    cik = str(issuer_cik).lstrip('0') or '0'
    acc_no_dashes = str(accession).replace('-', '').zfill(18)
    acc_with_dashes = _format_accession_with_dashes(accession)
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dashes}/{acc_with_dashes}-index.htm"


def _get_nested_value(data: Dict, *keys, default=None):
    """Safely get nested dictionary value."""
    current = data
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current if current is not None else default


def _parse_float(value: Any) -> Optional[float]:
    """Parse a value to float, returning None if not possible."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _get_footnotes(transaction: Dict) -> List[str]:
    """Extract all footnotes from a transaction."""
    footnotes = []

    # Check transactionCoding.footnote
    coding_footnotes = _get_nested_value(
        transaction, 'transactionCoding', 'footnote', default=[]
    )
    if isinstance(coding_footnotes, list):
        footnotes.extend(coding_footnotes)
    elif coding_footnotes:
        footnotes.append(str(coding_footnotes))

    # Check transactionAmounts for footnotes
    amounts = transaction.get('transactionAmounts', {})
    for key in ['transactionShares', 'transactionPricePerShare']:
        sub_footnotes = _get_nested_value(amounts, key, 'footnote', default=[])
        if isinstance(sub_footnotes, list):
            footnotes.extend(sub_footnotes)
        elif sub_footnotes:
            footnotes.append(str(sub_footnotes))

    # Check exerciseDate footnote (derivative)
    exercise_footnotes = _get_nested_value(
        transaction, 'exerciseDate', 'footnote', default=[]
    )
    if isinstance(exercise_footnotes, list):
        footnotes.extend(exercise_footnotes)
    elif exercise_footnotes:
        footnotes.append(str(exercise_footnotes))

    return footnotes


def _detect_10b5_1(footnotes: List[str]) -> bool:
    """Check if footnotes indicate a 10b5-1 plan."""
    for fn in footnotes:
        if RULE_10B5_1_RE.search(fn):
            return True
    return False


def _detect_tax_type(
    transaction_code: str,
    footnotes: List[str],
    price_present: bool
) -> Optional[str]:
    """Detect tax type from code and footnotes.

    Returns:
        "issuer" - tax withholding to issuer
        "open-market" - sell-to-cover on open market
        None - not a tax transaction
    """
    code = (transaction_code or "").upper()

    # Code F or D is always tax to issuer
    if code in ("F", "D"):
        return "issuer"

    # Check footnotes for tax keywords
    footnote_text = " ".join(footnotes)
    has_tax_keywords = TAX_RE.search(footnote_text) is not None
    has_issuer_keywords = ISSUER_RE.search(footnote_text) is not None

    if code == "S" and has_tax_keywords and price_present and not has_issuer_keywords:
        return "open-market"

    if has_tax_keywords and has_issuer_keywords:
        return "issuer"

    return None


def _extract_adoption_date(footnotes: List[str]) -> Optional[str]:
    """Extract plan adoption date from footnotes."""
    for fn in footnotes:
        if ADOPTION_RE.search(fn):
            # Look for ISO date
            match = DATE_ISO_RE.search(fn)
            if match:
                return match.group(0)
            # Look for text date
            match = DATE_MDY_RE.search(fn)
            if match:
                # Could parse to ISO, but keeping as-is for now
                return match.group(0)
    return None


def _parse_transaction(
    transaction: Dict,
    is_derivative: bool,
    issuer_info: Dict,
    owner_info: Dict,
    accession_number: str,
    filed_date: str = ''
) -> Dict[str, Any]:
    """Parse a single transaction into a row dict."""

    # Extract Datamule native fields
    transaction_code = _get_nested_value(
        transaction, 'transactionCoding', 'transactionCode', default=''
    )
    transaction_date = _get_nested_value(
        transaction, 'transactionDate', 'value', default=''
    )
    security_title = _get_nested_value(
        transaction, 'securityTitle', 'value', default=''
    )
    shares_raw = _get_nested_value(
        transaction, 'transactionAmounts', 'transactionShares', 'value'
    )
    price_raw = _get_nested_value(
        transaction, 'transactionAmounts', 'transactionPricePerShare', 'value'
    )
    acq_disp = _get_nested_value(
        transaction, 'transactionAmounts', 'transactionAcquiredDisposedCode', 'value', default=''
    )
    shares_after_raw = _get_nested_value(
        transaction, 'postTransactionAmounts', 'sharesOwnedFollowingTransaction', 'value'
    )
    direct_indirect = _get_nested_value(
        transaction, 'ownershipNature', 'directOrIndirectOwnership', 'value', default=''
    )

    # For derivatives, also check underlying shares
    underlying_shares_raw = _get_nested_value(
        transaction, 'underlyingSecurity', 'underlyingSecurityShares', 'value'
    )

    # Parse numeric values
    shares = _parse_float(shares_raw)
    price = _parse_float(price_raw)
    shares_after = _parse_float(shares_after_raw)
    underlying_shares = _parse_float(underlying_shares_raw)

    # Use underlying shares as fallback for derivative exercises
    if (shares is None or shares == 0) and underlying_shares:
        shares = underlying_shares

    # Get footnotes
    footnotes = _get_footnotes(transaction)

    # Calculate fields
    price_present = price is not None and price > 0

    # Signed shares (positive for acquire, negative for dispose)
    signed_shares = shares
    if shares is not None:
        if acq_disp.upper() == "D":
            signed_shares = -abs(shares)
        elif acq_disp.upper() == "A":
            signed_shares = abs(shares)
        else:
            # Fallback based on code
            if (transaction_code or "").upper() in ("S", "F", "G"):
                signed_shares = -abs(shares)
            else:
                signed_shares = abs(shares)

    # Transaction value
    transaction_value = None
    if shares is not None and price is not None and price > 0:
        transaction_value = round(abs(shares) * price, 2)

    # Detection
    is_10b5_1 = _detect_10b5_1(footnotes)
    tax_type = _detect_tax_type(transaction_code, footnotes, price_present)
    is_tax = tax_type is not None

    # Classification
    label = classify(transaction_code, is_10b5_1, tax_type)
    link_role = get_link_role(transaction_code, tax_type)

    # Plan adoption date (only if 10b5-1)
    plan_adoption_date = _extract_adoption_date(footnotes) if is_10b5_1 else None

    # Build SEC filing URL
    filing_url = _build_sec_filing_url(issuer_info.get('issuerCik', ''), accession_number)

    # Build row with Datamule native names + calculated fields
    return {
        # Datamule native fields
        'accessionNumber': accession_number,
        'filedDate': filed_date,
        'filingUrl': filing_url,
        'issuerCik': issuer_info.get('issuerCik', ''),
        'issuerName': issuer_info.get('issuerName', ''),
        'issuerTradingSymbol': issuer_info.get('issuerTradingSymbol', ''),
        'rptOwnerCik': owner_info.get('rptOwnerCik', ''),
        'rptOwnerName': owner_info.get('rptOwnerName', ''),
        'officerTitle': owner_info.get('officerTitle', ''),
        'securityTitle': security_title,
        'transactionDate': transaction_date,
        'transactionCode': transaction_code,
        'transactionShares': shares,
        'transactionPricePerShare': price,
        'transactionAcquiredDisposedCode': acq_disp,
        'sharesOwnedFollowingTransaction': shares_after,
        'directOrIndirectOwnership': direct_indirect,
        'footnote': footnotes,

        # Calculated fields
        'transactionValue': transaction_value,
        'signedShares': signed_shares,
        'is10b5_1': is_10b5_1,
        'isTax': is_tax,
        'taxType': tax_type,
        'label': label,
        'linkRole': link_role,
        'planAdoptionDate': plan_adoption_date,
        'secTable': 'Table 2' if is_derivative else 'Table 1',
        'rowType': 'SOURCE',
        'eventId': None,  # Set by rollup builder
    }


def parse_filing(filing_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Parse a filing into transaction rows.

    Args:
        filing_data: Dict from datamule_client.get_filing_data()
                    Contains 'accessionNumber', 'documentType', 'data', 'filedDate'

    Returns:
        List of transaction row dicts
    """
    accession_number = filing_data.get('accessionNumber', '')
    filed_date = filing_data.get('filedDate', '')
    doc_data = filing_data.get('data', {})
    ownership_doc = doc_data.get('ownershipDocument', {})

    # Extract issuer info
    issuer = ownership_doc.get('issuer', {})
    issuer_info = {
        'issuerCik': issuer.get('issuerCik', ''),
        'issuerName': issuer.get('issuerName', ''),
        'issuerTradingSymbol': issuer.get('issuerTradingSymbol', ''),
    }

    # Extract owner info
    reporting_owner = ownership_doc.get('reportingOwner', {})
    owner_id = reporting_owner.get('reportingOwnerId', {})
    owner_relationship = reporting_owner.get('reportingOwnerRelationship', {})
    owner_info = {
        'rptOwnerCik': owner_id.get('rptOwnerCik', ''),
        'rptOwnerName': owner_id.get('rptOwnerName', ''),
        'officerTitle': owner_relationship.get('officerTitle', ''),
    }

    rows = []

    # Parse non-derivative transactions
    nd_table = ownership_doc.get('nonDerivativeTable', {})
    nd_transactions = nd_table.get('nonDerivativeTransaction', [])
    if isinstance(nd_transactions, dict):
        nd_transactions = [nd_transactions]
    for txn in nd_transactions:
        row = _parse_transaction(
            txn, is_derivative=False,
            issuer_info=issuer_info, owner_info=owner_info,
            accession_number=accession_number, filed_date=filed_date
        )
        rows.append(row)

    # Parse derivative transactions
    d_table = ownership_doc.get('derivativeTable', {})
    d_transactions = d_table.get('derivativeTransaction', [])
    if isinstance(d_transactions, dict):
        d_transactions = [d_transactions]
    for txn in d_transactions:
        row = _parse_transaction(
            txn, is_derivative=True,
            issuer_info=issuer_info, owner_info=owner_info,
            accession_number=accession_number, filed_date=filed_date
        )
        rows.append(row)

    return rows
