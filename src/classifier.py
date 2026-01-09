"""Transaction classification logic.

Assigns human-readable labels to transactions based on:
- Transaction code (M, S, F, etc.)
- 10b5-1 plan status
- Tax type (issuer, open-market, or none)

Label priority:
1. Tax labels (highest priority)
2. 10b5-1 planned trades
3. Regular trades by code
4. Unknown (fallback)
"""


def classify(
    transaction_code: str,
    is_10b5_1: bool,
    tax_type: str = None
) -> str:
    """Classify a transaction and return human-readable label.

    Args:
        transaction_code: SEC transaction code (M, S, F, P, A, G, C, etc.)
        is_10b5_1: Whether transaction is under a 10b5-1 plan
        tax_type: "issuer", "open-market", or None

    Returns:
        Human-readable label string
    """
    code = (transaction_code or "").upper()

    # Tax takes highest priority
    if tax_type == "issuer":
        return "Tax - Sale to Issuer"
    if tax_type == "open-market":
        return "Tax - Open Market"

    # Sales
    if code == "S":
        if is_10b5_1:
            return "10b5-1 Planned Sale"
        return "Sale"

    # Purchases/Awards
    if code in ("P", "A"):
        if is_10b5_1:
            return "10b5-1 Planned Buy"
        return "Acquisition" if code == "A" else "Purchase"

    # Exercise
    if code == "M":
        return "Option Exercise"

    # Conversion
    if code == "C":
        return "Conversion"

    # Gift
    if code == "G":
        return "Gift"

    # Disposition to issuer
    if code == "D":
        return "Disposition to Issuer"

    # Tax withholding (code F)
    if code == "F":
        return "Tax Withholding"

    # Other known codes
    if code in ("I", "E", "H", "J", "K", "L", "O", "U", "V", "W", "X", "Z"):
        return "Other"

    # Fallback
    return "Unknown"


def get_link_role(
    transaction_code: str,
    tax_type: str = None
) -> str:
    """Determine the link role for roll-up grouping.

    Args:
        transaction_code: SEC transaction code
        tax_type: "issuer", "open-market", or None

    Returns:
        Link role: "exercise", "sale-common", "tax-sale-issuer",
                   "tax-sale-open", or "other"
    """
    code = (transaction_code or "").upper()

    # Exercise codes
    if code in ("M", "C", "X", "O"):
        return "exercise"

    # Sales
    if code == "S":
        if tax_type == "issuer":
            return "tax-sale-issuer"
        if tax_type == "open-market":
            return "tax-sale-open"
        return "sale-common"

    # Tax withholding codes (F, D)
    if code in ("F", "D"):
        return "tax-sale-issuer"

    return "other"
