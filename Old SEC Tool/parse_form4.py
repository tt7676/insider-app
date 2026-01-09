"""Parse Form 4 XML into SOURCE rows with initial classification.

Responsibilities:
- Emit every transaction row (Tables I & II)
- Robust share fallbacks
- ISO date parsing (YYYY-MM-DD)
- Plan Adoption Date extraction on SOURCE rows under a plan
- LinkRole assignment (exercise, sale-common, tax-sale-issuer, tax-sale-open, other)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from lxml import etree

from .classify import transaction_type_label_initial


# ---------------- Regex helpers ---------------- #
RULE_RE = re.compile(r"10b5-?1", re.IGNORECASE)
TAX_RE = re.compile(
    r"(withhold|withholding|withheld|tax(es)?|sell-?\s?to-?\s?cover|net\s+share\s+settle(ment)?|to\s+satisfy\s+(applicable\s+)?tax)",
    re.IGNORECASE,
)
ISSUER_RE = re.compile(
    r"(to\s+the\s+issuer|to\s+issuer|surrendered\s+to\s+(the\s+)?(issuer|company)|withheld\s+by\s+(the\s+)?issuer|tendered\s+to\s+(the\s+)?(issuer|company))",
    re.IGNORECASE,
)
ISO_D = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
MDY_S = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},\s*\d{4}\b",
    re.IGNORECASE,
)
NUM_MDY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def iso_date_from_mdy(txt: str) -> Optional[str]:
    """Convert 'MM/DD/YYYY' or 'Month D, YYYY' to 'YYYY-MM-DD'; pass through if already ISO."""
    if not txt:
        return None
    s = txt.strip()
    m = ISO_D.search(s)
    if m:
        return s[:10]
    m = NUM_MDY.search(s)
    if m:
        mm, dd, yyyy = map(int, m.groups())
        try:
            return datetime(yyyy, mm, dd).date().isoformat()
        except Exception:
            return None
    m = MDY_S.search(s)
    if m:
        token = m.group(0)
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(token, fmt).date().isoformat()
            except Exception:
                pass
    return None


def _parse_date_str(txt: str) -> Optional[str]:
    s = (txt or "").strip()
    m = ISO_D.search(s)
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return datetime(y, mo, d).date().isoformat()
        except Exception:
            return None
    m = MDY_S.search(s)
    if m:
        token = m.group(0)
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(token, fmt).date().isoformat()
            except Exception:
                pass
    m = NUM_MDY.search(s)
    if m:
        mm, dd, yyyy = map(int, m.groups())
        try:
            return datetime(yyyy, mm, dd).date().isoformat()
        except Exception:
            return None
    return None


def find_adoption_date_in_text(txt: str) -> Optional[str]:
    if not txt:
        return None
    hits: List[str] = []
    for m in re.finditer(
        r"(adopt(?:ed|ion)?|entered\s+into)", txt, flags=re.IGNORECASE
    ):
        start = max(0, m.start() - 50)
        end = min(len(txt), m.end() + 50)
        d = _parse_date_str(txt[start:end])
        if d:
            hits.append(d)
    if hits:
        return sorted(hits)[0]
    return _parse_date_str(txt)


def parse_transactions(xml_text: str, insider_display: str) -> Tuple[List[Dict], int]:
    """Parse a Form 4 XML into SOURCE rows and count of XML txns.

    - Emits every row (never drops rows)
    - Applies share fallbacks and signs by Acq/Disp
    - ISO dates for trade date
    - Extracts Plan Adoption Date on SOURCE rows under a plan
    - Sets LinkRole for downstream linking
    """
    root = etree.fromstring(xml_text.encode("utf-8"))

    issuer_name = (root.findtext(".//issuerName") or "").strip()
    issuer_ticker = (root.findtext(".//issuerTradingSymbol") or "").strip()
    role = (root.findtext(".//reportingOwner//officerTitle") or "").strip()

    # footnotes index
    footnotes: Dict[str, str] = {}
    for fn in root.findall(".//footnotes/footnote"):
        fid = fn.get("id")
        if fid:
            footnotes[fid] = "".join(fn.itertext()).strip()

    def val(tx, xpath: str) -> str:  # noqa: ANN001 - lxml element
        return (tx.findtext(xpath + "/value") or tx.findtext(xpath) or "").strip()

    def ref_ids(tx) -> List[str]:  # noqa: ANN001
        ids: List[str] = []
        for n in tx.findall(".//footnoteId"):
            if n.get("id"):
                ids.append(n.get("id"))
        ids += tx.xpath(".//*[@footnoteId]/@footnoteId")
        # unique, preserve order
        seen, out = set(), []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def has_plan(tx) -> bool:  # noqa: ANN001
        if any(RULE_RE.search(footnotes.get(fid, "")) for fid in ref_ids(tx)):
            return True
        for xp in [
            ".//transactionCoding/pursuantToRule10b5-1",
            ".//transactionCoding/pursuantToRule10b5_1",
            ".//transactionCoding/rule10b5-1",
            ".//transactionCoding/rule10b5_1",
            ".//*[contains(local-name(),'10b5') and contains(local-name(),'1')]",
        ]:
            vals = [t.strip().lower() for t in tx.xpath(f"{xp}//text()")]
            if any(v in ("1", "true", "y", "yes", "x") for v in vals):
                return True
        return False

    def tax_flags(
        tx, code: str, price_present: bool
    ) -> Tuple[bool, bool]:  # noqa: ANN001
        c = (code or "").upper()
        if c in ("F", "D"):
            return (False, True)
        texts = [footnotes.get(fid, "") for fid in ref_ids(tx)]
        any_tax = any(TAX_RE.search(t) for t in texts)
        issuerish = any(ISSUER_RE.search(t) for t in texts)
        if c == "S" and any_tax and price_present and not issuerish:
            return (True, False)  # open-market sell-to-cover
        if any_tax and issuerish:
            return (False, True)  # surrender/withhold to issuer
        return (False, False)

    def adoption_date_from_tx(tx) -> Optional[str]:  # noqa: ANN001
        # 1) tags containing 'adopt' near the tx
        for node in tx.xpath(". | ancestor::*"):
            for cn in node.xpath(
                ".//*[contains(translate(local-name(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'adopt')]"
            ):
                d = find_adoption_date_in_text(" ".join(cn.itertext()))
                if d:
                    return d
        # 2) referenced footnotes
        for fid in ref_ids(tx):
            d = find_adoption_date_in_text(footnotes.get(fid, ""))
            if d:
                return d
        return None

    rows: List[Dict] = []
    nd_nodes = root.findall(".//nonDerivativeTable/nonDerivativeTransaction")
    d_nodes = root.findall(".//derivativeTable/derivativeTransaction")
    xml_txn_count = len(nd_nodes) + len(d_nodes)

    def robust_shares(tx) -> Optional[float]:  # noqa: ANN001
        s = pd.to_numeric(val(tx, ".//transactionShares"), errors="coerce")
        if (s is None) or (pd.isna(s)) or (s == 0):
            s = pd.to_numeric(val(tx, ".//underlyingSecurityShares"), errors="coerce")
        if (s is None) or (pd.isna(s)) or (s == 0):
            s = pd.to_numeric(
                val(tx, ".//numberOfDerivativeSecuritiesAcquiredOrDisposed"),
                errors="coerce",
            )
        return s

    def emit(tx, is_deriv: bool):  # noqa: ANN001
        code = (tx.findtext(".//transactionCoding/transactionCode") or "").strip()
        raw_dt = val(tx, ".//transactionDate")
        iso_dt = iso_date_from_mdy(raw_dt) or raw_dt or ""
        title = val(tx, ".//securityTitle")
        shares = robust_shares(tx)
        price = pd.to_numeric(val(tx, ".//transactionPricePerShare"), errors="coerce")
        post = pd.to_numeric(
            (tx.findtext(".//sharesOwnedFollowingTransaction/value") or "").strip(),
            errors="coerce",
        )
        acqdsp = (tx.findtext(".//transactionAcquiredDisposedCode/value") or "").strip()

        signed = shares
        if pd.notna(shares):
            if acqdsp.upper() == "D":
                signed = -abs(shares)
            elif acqdsp.upper() == "A":
                signed = abs(shares)
            else:
                signed = (
                    -abs(shares)
                    if (code or "").upper() in ("S", "F", "G")
                    else abs(shares)
                )

        plan = has_plan(tx)
        tax_open, tax_issuer = tax_flags(tx, code, pd.notna(price))
        label = transaction_type_label_initial(code, plan, tax_open, tax_issuer)

        # LinkRole
        cu = (code or "").upper()
        if cu in ("M", "C", "X", "O"):
            link_role = "exercise"
        elif cu == "S":
            if tax_issuer:
                link_role = "tax-sale-issuer"
            elif tax_open:
                link_role = "tax-sale-open"
            else:
                link_role = "sale-common"
        elif cu in ("F", "D"):
            link_role = "tax-sale-issuer"
        else:
            link_role = "other"

        plan_adopt = adoption_date_from_tx(tx) if plan else None

        rows.append(
            {
                "Holder Name": insider_display,
                "Trade Date Range": iso_dt,
                "Security Type": title,
                "Transacted Shares": signed if signed is not None else shares,
                "Transaction Value Range ($)": (
                    float(round(abs(shares) * price, 2))
                    if pd.notna(shares) and pd.notna(price)
                    else None
                ),
                "Transaction Type": label,
                "SEC Transaction Code": code,
                "Price Range ($)": price,
                "End of Filing Shares": post,
                "% Change": None,
                "Filed Date": None,  # set later
                "Source": "",
                "accession": "",
                "RowTag": "SOURCE",
                "EventID": "",
                "LinkRole": link_role,
                "AD_Flag": acqdsp,
                "AcqDisp": acqdsp,
                "Rule 10b5-1 Plan": bool(plan),
                "Tax-Related": bool(tax_open or tax_issuer),
                "_is_derivative": is_deriv,
                "_issuer_name": issuer_name,
                "_issuer_ticker": issuer_ticker,
                "_owner_role": role,
                "_price_present": pd.notna(price),
                "Plan Adoption Date": plan_adopt,
            }
        )

    for tx in nd_nodes:
        emit(tx, is_deriv=False)
    for tx in d_nodes:
        emit(tx, is_deriv=True)

    return rows, xml_txn_count
