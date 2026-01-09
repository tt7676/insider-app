"""Per-row classification helpers (initial labeling only).

This module contains only the initial label precedence function. Other
classification helpers (plan and tax detection, link roles) live in
``src.parse_form4`` during the parsing phase.
"""


def transaction_type_label_initial(
    code: str, is_planned: bool, tax_open: bool, tax_issuer: bool
) -> str:
    """Initial label before roll-up linkage upgrades planned sales.

    Precedence:
    - Tax (issuer first, then open-market)
    - 10b planned sale (Common; may upgrade later)
    - Sale (non-plan)
    - Exercise / Conversion / Gift / Acquisition
    - Other / Unknown
    """
    c = (code or "").upper()
    if tax_issuer:
        return "Tax - Sale to Issuer"
    if tax_open:
        return "Tax - Open Market"
    if c == "S" and is_planned:
        return "10b Planned Sale - Common stock"
    if c == "S":
        return "Sale (non tax or 10b)"
    if c in ("P", "A"):
        return "10b Plan Buy" if is_planned else "acquisition"
    if c == "M":
        return "Option Exercise"
    if c == "C":
        return "Conversion"
    if c == "G":
        return "Gift"
    if c in ("D", "F", "I", "E", "H", "J", "K", "L", "O", "U", "V", "W", "X", "Z"):
        return "Other"
    return "Unknown"
