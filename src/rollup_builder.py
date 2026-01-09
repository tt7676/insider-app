"""Build roll-up summaries for related transactions.

Groups exercise transactions with related sales:
- Links exercises to sales on the same date
- Calculates match status (shares exercised vs sold)
- Handles splits when sale exceeds exercise amount (creates SYNTHETIC rows)
- Creates ROLLUP summary rows for Exercise-Sale and Automatic Disposition
"""

from typing import Dict, List, Any, Tuple
from copy import deepcopy


# Tolerance for matching exercise to sales
TOLERANCE_ABSOLUTE = 5  # shares
TOLERANCE_PERCENT = 0.005  # 0.5%


def _calculate_match_status(
    exercise_est: float,
    sold_sum: float
) -> Tuple[str, float, bool]:
    """Calculate match status between exercise and sales.

    Returns:
        (match_status, match_delta, tolerance_used)
    """
    match_delta = sold_sum - exercise_est
    max_total = max(abs(exercise_est), abs(sold_sum))
    tolerance_threshold = max(
        TOLERANCE_ABSOLUTE,
        TOLERANCE_PERCENT * max_total
    ) if max_total > 0 else 0

    if match_delta == 0:
        return "EXACT_MATCH", match_delta, False
    elif abs(match_delta) <= tolerance_threshold:
        return "WITHIN_TOLERANCE", match_delta, True
    else:
        return "MISMATCH", match_delta, False


def _get_filing_order_index(transactions: List[Dict], txn: Dict) -> int:
    """Get the original filing order index for a transaction."""
    try:
        return transactions.index(txn)
    except ValueError:
        return 9999


def build_rollups(transactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build roll-up summaries for exercise events.

    Creates:
    - Exercise - Sale / Exercise - Hold rollups for exercise events
    - Automatic Disposition rollup for unlinked sales
    - SYNTHETIC rows for split sales

    Args:
        transactions: List of parsed transaction dicts (from transaction_parser)

    Returns:
        List with ROLLUP, SOURCE, and SYNTHETIC rows in waterfall order
    """
    if not transactions:
        return []

    # Keep original order for reference
    original_order = {id(t): i for i, t in enumerate(transactions)}

    # Separate by link role
    exercises = [t for t in transactions if t.get('linkRole') == 'exercise']
    sales_common = [t for t in transactions if t.get('linkRole') == 'sale-common']
    tax_rows = [t for t in transactions if t.get('linkRole') in ('tax-sale-issuer', 'tax-sale-open')]
    other_rows = [t for t in transactions if t.get('linkRole') == 'other']

    # If no exercises, return original list in filing order (no rollups)
    if not exercises:
        for t in transactions:
            t['rowType'] = 'SOURCE'
        return sorted(transactions, key=lambda x: original_order.get(id(x), 9999))

    # Get accession from first transaction
    accession = str(transactions[0].get('accessionNumber', ''))

    # Track processed items
    processed_sales = set()  # IDs of fully processed sales
    synthetic_rows: List[Dict] = []  # Split rows we create

    # Collect all exercises and linked sales for the Exercise rollup
    all_exercise_rows: List[Dict] = []
    all_linked_sales: List[Dict] = []
    total_exercise_est = 0.0
    total_sale_value = 0.0
    exercise_method = "UNKNOWN"

    # Process all exercises together (Option B - combined)
    for ex in exercises:
        all_exercise_rows.append(ex)

    # Calculate total exercise estimate
    # Method A: Sum of non-derivative acquisitions
    underlying_a = sum(
        abs(t.get('transactionShares') or 0)
        for t in transactions
        if t.get('secTable') == 'Table 1'
        and (t.get('transactionAcquiredDisposedCode') or '').upper() == 'A'
        and t.get('linkRole') == 'exercise'
    )

    if underlying_a > 0:
        total_exercise_est = underlying_a
        exercise_method = "UNDERLYING_A"
    else:
        # Method B: Sum of derivative exercises
        total_exercise_est = sum(abs(t.get('transactionShares') or 0) for t in exercises)
        exercise_method = "DERV_1to1" if total_exercise_est > 0 else "UNKNOWN"

    # Link sales greedily in filing order
    remaining = total_exercise_est
    sales_in_order = sorted(sales_common, key=lambda x: original_order.get(id(x), 9999))

    for sale in sales_in_order:
        if remaining <= 0:
            break

        sale_shares = abs(sale.get('transactionShares') or 0)
        if sale_shares == 0:
            continue

        sale_price = sale.get('transactionPricePerShare')

        if sale_shares <= remaining:
            # Fully link this sale
            processed_sales.add(id(sale))

            # Upgrade label if 10b5-1 and linked to exercise
            if sale.get('is10b5_1') and sale.get('label') == '10b5-1 Planned Sale':
                sale['label'] = '10b5-1 Planned Sale (Derivative)'

            all_linked_sales.append(sale)

            if sale_price is not None:
                total_sale_value += sale_shares * sale_price

            remaining -= sale_shares

        else:
            # Split: create two SYNTHETIC rows
            linked_amount = remaining
            residual_amount = sale_shares - remaining

            # Create SYNTHETIC linked portion
            sale_linked = deepcopy(sale)
            sale_linked['transactionShares'] = linked_amount
            sale_linked['signedShares'] = -linked_amount
            sale_linked['rowType'] = 'SYNTHETIC'
            if sale_price is not None:
                sale_linked['transactionValue'] = round(linked_amount * sale_price, 2)

            if sale_linked.get('is10b5_1') and sale_linked.get('label') == '10b5-1 Planned Sale':
                sale_linked['label'] = '10b5-1 Planned Sale (Derivative)'

            all_linked_sales.append(sale_linked)
            synthetic_rows.append(sale_linked)

            if sale_price is not None:
                total_sale_value += linked_amount * sale_price

            # Create SYNTHETIC residual portion
            sale_residual = deepcopy(sale)
            sale_residual['transactionShares'] = residual_amount
            sale_residual['signedShares'] = -residual_amount
            sale_residual['rowType'] = 'SYNTHETIC'
            if sale_price is not None:
                sale_residual['transactionValue'] = round(residual_amount * sale_price, 2)
            # Residual keeps original label (not upgraded to Derivative)

            synthetic_rows.append(sale_residual)

            # Mark original as processed (it's replaced by two synthetics)
            processed_sales.add(id(sale))

            remaining = 0
            break

    # Calculate match status
    linked_sum_shares = sum(abs(s.get('transactionShares') or 0) for s in all_linked_sales)
    match_status, match_delta, tolerance_used = _calculate_match_status(
        total_exercise_est, linked_sum_shares
    )

    # Determine aggregate type for exercise rollup
    aggregate_type = "Exercise - Sale" if linked_sum_shares > 0 else "Exercise - Hold"

    # Calculate date and price ranges for exercise rollup
    all_dates = [t.get('transactionDate') for t in all_exercise_rows + all_linked_sales if t.get('transactionDate')]
    all_prices = [t.get('transactionPricePerShare') for t in all_exercise_rows + all_linked_sales
                  if t.get('transactionPricePerShare') is not None]

    trade_date_start = min(all_dates) if all_dates else ''
    trade_date_end = max(all_dates) if all_dates else ''
    price_range_min = min(all_prices) if all_prices else None
    price_range_max = max(all_prices) if all_prices else None

    # Check for tax rows
    has_tax_rows = len(tax_rows) > 0

    # Generate event ID for exercise rollup
    event_id_exercise = f"{accession.replace('-', '')}-EXERCISE-01"

    # Build Exercise ROLLUP row
    exercise_rollup = {
        # Metadata
        'accessionNumber': accession,
        'filedDate': transactions[0].get('filedDate', ''),
        'filingUrl': all_exercise_rows[0].get('filingUrl', '') if all_exercise_rows else '',
        'issuerCik': transactions[0].get('issuerCik', ''),
        'issuerName': transactions[0].get('issuerName', ''),
        'issuerTradingSymbol': transactions[0].get('issuerTradingSymbol', ''),
        'rptOwnerCik': transactions[0].get('rptOwnerCik', ''),
        'rptOwnerName': transactions[0].get('rptOwnerName', ''),
        'officerTitle': transactions[0].get('officerTitle', ''),

        # Roll-up specific fields
        'rowType': 'ROLLUP',
        'eventId': event_id_exercise,
        'aggregateType': aggregate_type,
        'aggregateShares': -abs(linked_sum_shares) if linked_sum_shares else 0,
        'aggregateValue': total_sale_value if linked_sum_shares else None,
        'priceRangeMin': price_range_min,
        'priceRangeMax': price_range_max,
        'tradeDateStart': trade_date_start,
        'tradeDateEnd': trade_date_end,
        'exerciseSharesEst': total_exercise_est,
        'exerciseSharesMethod': exercise_method,
        'soldNonTaxSum': linked_sum_shares,
        'matchDelta': match_delta,
        'matchStatus': match_status,
        'toleranceUsed': tolerance_used,
        'hasTaxRows': has_tax_rows,
        'linkedTxnCount': len(all_exercise_rows) + len(all_linked_sales),

        # Standard fields (for CSV compatibility)
        'securityTitle': 'Class A Common Stock',
        'transactionDate': f"{trade_date_start} - {trade_date_end}" if trade_date_start != trade_date_end else trade_date_start,
        'transactionCode': '',
        'transactionShares': -abs(linked_sum_shares) if linked_sum_shares else 0,
        'transactionPricePerShare': None,
        'transactionAcquiredDisposedCode': 'D',
        'transactionValue': total_sale_value if linked_sum_shares else None,
        'signedShares': -abs(linked_sum_shares) if linked_sum_shares else 0,
        'is10b5_1': any(s.get('is10b5_1') for s in all_linked_sales),
        'isTax': False,
        'taxType': None,
        'label': aggregate_type,
        'linkRole': '',
        'secTable': 'Table 1',
    }

    # Mark exercise and linked sales with event ID
    for row in all_exercise_rows:
        row['eventId'] = event_id_exercise
        row['rowType'] = 'SOURCE'

    for row in all_linked_sales:
        row['eventId'] = event_id_exercise
        if row not in synthetic_rows:
            row['rowType'] = 'SOURCE'

    # Collect unlinked sales (including synthetic residuals)
    unlinked_sales: List[Dict] = []

    # Add unprocessed original sales
    for sale in sales_common:
        if id(sale) not in processed_sales:
            sale['rowType'] = 'SOURCE'
            unlinked_sales.append(sale)

    # Add synthetic residuals (not linked)
    for syn in synthetic_rows:
        if syn not in all_linked_sales:
            unlinked_sales.append(syn)

    # Build Automatic Disposition rollup if there are unlinked sales
    auto_disp_rollup = None
    event_id_auto = f"{accession.replace('-', '')}-AUTODISP-01"

    if unlinked_sales:
        unlinked_sum_shares = sum(abs(s.get('transactionShares') or 0) for s in unlinked_sales)
        unlinked_value_sum = sum(
            abs(s.get('transactionShares') or 0) * (s.get('transactionPricePerShare') or 0)
            for s in unlinked_sales
            if s.get('transactionPricePerShare') is not None
        )
        unlinked_prices = [s.get('transactionPricePerShare') for s in unlinked_sales
                          if s.get('transactionPricePerShare') is not None]
        unlinked_dates = [s.get('transactionDate') for s in unlinked_sales if s.get('transactionDate')]

        auto_disp_rollup = {
            # Metadata
            'accessionNumber': accession,
            'filedDate': transactions[0].get('filedDate', ''),
            'filingUrl': unlinked_sales[0].get('filingUrl', '') if unlinked_sales else '',
            'issuerCik': transactions[0].get('issuerCik', ''),
            'issuerName': transactions[0].get('issuerName', ''),
            'issuerTradingSymbol': transactions[0].get('issuerTradingSymbol', ''),
            'rptOwnerCik': transactions[0].get('rptOwnerCik', ''),
            'rptOwnerName': transactions[0].get('rptOwnerName', ''),
            'officerTitle': transactions[0].get('officerTitle', ''),

            # Roll-up specific fields
            'rowType': 'ROLLUP',
            'eventId': event_id_auto,
            'aggregateType': 'Automatic Disposition',
            'aggregateShares': -abs(unlinked_sum_shares),
            'aggregateValue': unlinked_value_sum if unlinked_value_sum > 0 else None,
            'priceRangeMin': min(unlinked_prices) if unlinked_prices else None,
            'priceRangeMax': max(unlinked_prices) if unlinked_prices else None,
            'tradeDateStart': min(unlinked_dates) if unlinked_dates else '',
            'tradeDateEnd': max(unlinked_dates) if unlinked_dates else '',
            'exerciseSharesEst': None,
            'exerciseSharesMethod': None,
            'soldNonTaxSum': unlinked_sum_shares,
            'matchDelta': None,
            'matchStatus': None,
            'toleranceUsed': None,
            'hasTaxRows': False,
            'linkedTxnCount': len(unlinked_sales),

            # Standard fields
            'securityTitle': 'Class A Common Stock',
            'transactionDate': f"{min(unlinked_dates)} - {max(unlinked_dates)}" if unlinked_dates and min(unlinked_dates) != max(unlinked_dates) else (unlinked_dates[0] if unlinked_dates else ''),
            'transactionCode': '',
            'transactionShares': -abs(unlinked_sum_shares),
            'transactionPricePerShare': None,
            'transactionAcquiredDisposedCode': 'D',
            'transactionValue': unlinked_value_sum if unlinked_value_sum > 0 else None,
            'signedShares': -abs(unlinked_sum_shares),
            'is10b5_1': any(s.get('is10b5_1') for s in unlinked_sales),
            'isTax': False,
            'taxType': None,
            'label': 'Automatic Disposition',
            'linkRole': '',
            'secTable': 'Table 1',
        }

        # Mark unlinked sales with auto disposition event ID
        for row in unlinked_sales:
            row['eventId'] = event_id_auto

    # Build output in waterfall order
    output: List[Dict[str, Any]] = []

    # 1. Exercise rollup
    output.append(exercise_rollup)

    # 2. Exercise SOURCE rows: Table 2 (derivative) first, then Table 1 (non-derivative), in filing order
    derivative_exercises = sorted(
        [r for r in all_exercise_rows if r.get('secTable') == 'Table 2'],
        key=lambda x: original_order.get(id(x), 9999)
    )
    non_derivative_exercises = sorted(
        [r for r in all_exercise_rows if r.get('secTable') == 'Table 1'],
        key=lambda x: original_order.get(id(x), 9999)
    )
    output.extend(derivative_exercises)
    output.extend(non_derivative_exercises)

    # 3. Linked sales in filing order
    linked_sales_ordered = sorted(
        all_linked_sales,
        key=lambda x: original_order.get(id(x), 9999) if id(x) in original_order else 9999
    )
    output.extend(linked_sales_ordered)

    # 4. Automatic Disposition rollup (if exists)
    if auto_disp_rollup:
        output.append(auto_disp_rollup)

        # 5. Unlinked sales in filing order
        unlinked_sales_ordered = sorted(
            unlinked_sales,
            key=lambda x: original_order.get(id(x), 9999) if id(x) in original_order else 9999
        )
        output.extend(unlinked_sales_ordered)

    # 6. Tax rows (in filing order)
    for row in tax_rows:
        row['rowType'] = 'SOURCE'
    tax_rows_ordered = sorted(tax_rows, key=lambda x: original_order.get(id(x), 9999))
    output.extend(tax_rows_ordered)

    # 7. Other rows (in filing order)
    for row in other_rows:
        row['rowType'] = 'SOURCE'
    other_rows_ordered = sorted(other_rows, key=lambda x: original_order.get(id(x), 9999))
    output.extend(other_rows_ordered)

    return output
