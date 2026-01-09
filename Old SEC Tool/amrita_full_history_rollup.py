#!/usr/bin/env python3
"""
Amrita Ahuja (Block, Inc.) — Full history Form 4 -> ONE-TAB CSV with roll-ups

WHAT’S IN THIS SCRIPT (2025-09; latest):
- Robust per-row emission: we NEVER drop a Form 4 transaction row.
  * We read shares with fallbacks (both tables):
      transactionShares
   -> underlyingSecurityShares
   -> numberOfDerivativeSecuritiesAcquiredOrDisposed
- ISO dates in output (YYYY-MM-DD) for Trade Date + Filed Date.
- Plan Adoption Date (Form 4 only) on SOURCE rows (from tags/footnotes mentioning “adopt/entered into …”).
- Tax precedence in labeling (Tax beats 10b).
- 10b split logic:
  * Planned Table-I S rows linked to same-filing exercise → “10b Planned Sale - Derivative”.
  * Planned S rows NOT linked → “10b Planned Sale - Common stock”.
- NEW: Split the final S row if linking it would overshoot the exercised amount:
  * Linked piece = amount required to reach exercise estimate → label as 10b…-Derivative.
  * Residual piece remains as 10b…-Common stock (or Sale non-plan).
- Roll-ups:
  * Only link Table-I non-tax S (“sale-common”) to exercises.
  * Roll-up label uses hyphen: “Exercise - Sale” / “Exercise - Hold”.
  * Roll-up $ = sum of linked sale lots’ values.
  * Match status (EXACT / WITHIN_TOLERANCE / MISMATCH).
- Completeness guard:
  * For each filing, compare XML transaction node count to emitted SOURCE rows; if mismatch,
    add a visible SOURCE row “XML PARSE WARNING” and print a console warning.

USAGE
  pip install requests lxml pandas
  python amrita_full_history_rollup.py --mode full   --outfile amrita_form4.csv
  python amrita_full_history_rollup.py --mode update --outfile amrita_form4.csv
"""

import argparse
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from lxml import etree

try:
    from src.parse_form4 import parse_transactions as _parse_transactions_new
except ModuleNotFoundError:
    import importlib.util as _ilu
    import os as _os
    import sys as _sys
    import types as _types

    _root = _os.path.dirname(__file__)
    _src_dir = _os.path.join(_root, "src")

    # Seed package context for relative imports inside src.* modules
    if "src" not in _sys.modules:
        _pkg = _types.ModuleType("src")
        _pkg.__path__ = [_src_dir]  # type: ignore[attr-defined]
        _sys.modules["src"] = _pkg

    # Preload src.classify
    _classify_path = _os.path.join(_src_dir, "classify.py")
    _spec_c = _ilu.spec_from_file_location("src.classify", _classify_path)
    assert _spec_c and _spec_c.loader
    _mod_c = _ilu.module_from_spec(_spec_c)
    _spec_c.loader.exec_module(_mod_c)  # type: ignore[attr-defined]
    _sys.modules["src.classify"] = _mod_c

    # Load src.parse_form4 (which imports .classify)
    _parse_path = _os.path.join(_src_dir, "parse_form4.py")
    _spec_p = _ilu.spec_from_file_location("src.parse_form4", _parse_path)
    assert _spec_p and _spec_p.loader
    _mod_p = _ilu.module_from_spec(_spec_p)
    _spec_p.loader.exec_module(_mod_p)  # type: ignore[attr-defined]
    _parse_transactions_new = _mod_p.parse_transactions  # type: ignore[attr-defined]

# ---------------- Config ---------------- #
UA = {
    "User-Agent": "DecadePartners-InsiderTool/1.0 (your-email@example.com)"
}  # <- set your email
BASE = "https://www.sec.gov"
TIMEOUT = 20
SLEEP_SEC = 0.20
XML_CACHE_DIR = "xml_cache"
BLOCK_CIK10 = "0001512673"  # Block, Inc. (Square)

# Matching tolerance for roll-ups
TOL_ABS = 5  # shares
TOL_PCT = 0.005  # 0.5% of max(exercise_est, sold)

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


# ---------------- HTTP helpers ---------------- #
def fetch_text(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        return r.text if r.ok else None
    except requests.RequestException:
        return None


def fetch_json(url: str) -> Optional[dict]:
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def head_ok(url: str) -> bool:
    try:
        r = requests.head(url, headers=UA, timeout=TIMEOUT, allow_redirects=True)
        return r.ok
    except requests.RequestException:
        return False


# ---------------- Filing enumeration ---------------- #
def list_all_form4_filings(cik10: str) -> List[Dict]:
    rows: List[Dict] = []
    root = fetch_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    if not root:
        return rows

    def harvest(rec: dict):
        forms = rec.get("form", [])
        acc = rec.get("accessionNumber", [])
        prim = rec.get("primaryDocument", [])
        filed = rec.get("filingDate", [])
        for i, f in enumerate(forms):
            if f in ("4", "4/A"):
                rows.append(
                    {"accession": acc[i], "primary": prim[i], "filed_at": filed[i]}
                )

    harvest(root.get("filings", {}).get("recent", {}))
    for f in root.get("filings", {}).get("files", []):
        older = fetch_json(f"https://data.sec.gov/submissions/{f.get('name')}")
        if older:
            harvest(older.get("filings", {}).get("recent", {}))

    seen, uniq = set(), []
    for r in sorted(rows, key=lambda x: x["filed_at"], reverse=True):
        if r["accession"] in seen:
            continue
        seen.add(r["accession"])
        uniq.append(r)
    return list(reversed(uniq))


# ---------------- Cache / fetch XML ---------------- #
def cache_path(accession: str) -> str:
    os.makedirs(XML_CACHE_DIR, exist_ok=True)
    return os.path.join(XML_CACHE_DIR, f"{accession.replace('/', '_')}.xml")


def fetch_xml_for_accession(
    cik10: str, accession: str, primary: str
) -> Tuple[Optional[str], bool]:
    cp = cache_path(accession)
    if os.path.exists(cp):
        try:
            txt = open(cp, "r", encoding="utf-8").read()
            if txt and "<ownershipDocument" in txt:
                return txt, True
        except Exception:
            pass

    acc_path = accession.replace("-", "")
    base_dir = f"{BASE}/Archives/edgar/data/{int(cik10)}/{acc_path}"

    txt = fetch_text(f"{base_dir}/{primary}")
    if txt and txt.strip().startswith("<") and "<ownershipDocument" in txt:
        try:
            open(cp, "w", encoding="utf-8").write(txt)
        except Exception:
            pass
        return txt, False

    idx = fetch_json(f"{base_dir}/index.json")
    if idx:
        for it in idx.get("directory", {}).get("item", []):
            nm = it.get("name", "").lower()
            if nm.endswith(".xml"):
                cand = fetch_text(f"{base_dir}/{it['name']}")
                if (
                    cand
                    and cand.strip().startswith("<")
                    and "<ownershipDocument" in cand
                ):
                    try:
                        open(cp, "w", encoding="utf-8").write(cand)
                    except Exception:
                        pass
                    return cand, False
    return None, False


def form4_doc_url(cik10: str, accession: str) -> str:
    acc_path = accession.replace("-", "")
    base_dir = f"{BASE}/Archives/edgar/data/{int(cik10)}/{acc_path}"
    idx = fetch_json(f"{base_dir}/index.json")
    html_candidate = xml_candidate = None
    if idx:
        items = idx.get("directory", {}).get("item", [])
        for it in items:
            nm = it.get("name", "")
            nml = nm.lower()
            if nml.endswith((".htm", ".html")) and (
                "form4" in nml or "f345" in nml or "doc4" in nml
            ):
                html_candidate = f"{base_dir}/{nm}"
                break
        if not html_candidate:
            for it in items:
                nml = it.get("name", "").lower()
                if nml.endswith((".htm", ".html")):
                    html_candidate = f"{base_dir}/{it['name']}"
                    break
        for it in items:
            nml = it.get("name", "").lower()
            if nml.endswith(".xml") and (
                "form4" in nml or "f345" in nml or "doc4" in nml
            ):
                xml_candidate = f"{base_dir}/{it['name']}"
                break
    if html_candidate and head_ok(html_candidate):
        return html_candidate
    if xml_candidate and head_ok(xml_candidate):
        return xml_candidate
    if head_ok(f"{base_dir}/{accession}-index-headers.html"):
        return f"{base_dir}/{accession}-index-headers.html"
    if head_ok(f"{base_dir}/{accession}-index.html"):
        return f"{base_dir}/{accession}-index.html"
    return f"{base_dir}/index.html"


# ---------------- Utility ---------------- #
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


# ---------------- Classification helpers ---------------- #
def transaction_type_label_initial(
    code: str, plan: bool, tax_open: bool, tax_issuer: bool
) -> str:
    """Initial label before roll-up linkage logic upgrades planned S to Derivative when linked."""
    c = (code or "").upper()
    if tax_issuer:
        return "Tax - Sale to Issuer"
    if tax_open:
        return "Tax - Open Market"
    if c == "S" and plan:
        return "10b Planned Sale - Common stock"  # may be upgraded after linking
    if c == "S":
        return "Sale (non tax or 10b)"
    if c in ("P", "A"):
        return "10b Plan Buy" if plan else "acquisition"
    if c == "M":
        return "Option Exercise"
    if c == "C":
        return "Conversion"
    if c == "G":
        return "Gift"
    if c in ("D", "F", "I", "E", "H", "J", "K", "L", "O", "U", "V", "W", "X", "Z"):
        return "Other"
    return "Unknown"


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
    hits = []
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


# ---------------- Parse a filing into SOURCE rows ---------------- #
def parse_transactions(xml_text: str, insider_display: str) -> Tuple[List[Dict], int]:
    # Delegate to the migrated implementation to keep behavior in sync.
    return _parse_transactions_new(xml_text, insider_display)


# ---------------- Roll-up builder (with SPLIT logic) ---------------- #
def build_events_and_rollups(
    rows: List[Dict], accession: str, filed_iso: str
) -> List[Dict]:
    """
    Link exercises -> Table-I, non-tax S rows (sale-common).
    If last S would overshoot exercised amount, split into linked + residual.
    Upgrade linked planned S to "10b Planned Sale - Derivative"; residual stays as planned-common (or non-plan).
    """
    exercises = [r for r in rows if r["LinkRole"] == "exercise"]
    sales_common = [r for r in rows if r["LinkRole"] == "sale-common"]
    tax_rows = [
        r for r in rows if r["LinkRole"] in ("tax-sale-issuer", "tax-sale-open")
    ]

    if not exercises:
        return rows

    # group exercises by trade date
    ex_by_date: Dict[str, List[Dict]] = {}
    for r in exercises:
        ex_by_date.setdefault(r["Trade Date Range"], []).append(r)

    sales_pool = sorted(sales_common, key=lambda x: x["Trade Date Range"])
    out: List[Dict] = []
    event_seq = 1

    for ex_date in sorted(ex_by_date.keys()):
        ex_rows = ex_by_date[ex_date]

        # Exercise estimate: Method A (underlying A on date) -> Method B (sum abs(exercises))
        underlying_A = sum(
            abs(r["Transacted Shares"])
            for r in rows
            if (not r["_is_derivative"])
            and (r.get("AcqDisp") or "").upper() == "A"
            and r["Trade Date Range"] == ex_date
        )
        if underlying_A > 0:
            ex_est, ex_method = underlying_A, "UNDERLYING_A"
        else:
            ex_est = sum(abs(r["Transacted Shares"] or 0) for r in ex_rows)
            ex_method = "DERV_1to1" if ex_est > 0 else "UNKNOWN"

        # Link sales greedily and SPLIT last if overshoot
        remaining = ex_est
        linked_sales: List[Dict] = []
        sale_value_sum = 0.0

        for s in sales_pool:
            if s.get("_assigned_event"):
                continue
            if s["Trade Date Range"] < ex_date:
                continue

            s_abs = abs(s.get("Transacted Shares") or 0) or 0.0
            if s_abs == 0:
                # zero-amount sale row; do not link, leave as-is
                continue

            if remaining <= 0:
                # Already satisfied; leave this sale as common-stock residual
                continue

            price = s.get("Price Range ($)")
            if s_abs <= remaining:
                # fully link
                s["_assigned_event"] = True  # original row becomes linked
                # upgrade label if planned and non-tax
                if (
                    s.get("Rule 10b5-1 Plan")
                    and s["Transaction Type"] == "10b Planned Sale - Common stock"
                ):
                    s["Transaction Type"] = "10b Planned Sale - Derivative"
                linked_sales.append(s)
                if price is not None:
                    try:
                        sale_value_sum += float(s_abs) * float(price)
                    except Exception:
                        pass
                remaining -= s_abs
            else:
                # SPLIT into linked + residual
                linked_needed = remaining
                residual_abs = s_abs - remaining

                # make a copy for the linked piece
                s_link = dict(s)
                s_link["Transacted Shares"] = -linked_needed
                if price is not None:
                    try:
                        s_link["Transaction Value Range ($)"] = float(
                            linked_needed
                        ) * float(price)
                    except Exception:
                        s_link["Transaction Value Range ($)"] = s.get(
                            "Transaction Value Range ($)"
                        )
                # mark linked
                s_link["_assigned_event"] = True
                if (
                    s_link.get("Rule 10b5-1 Plan")
                    and s_link["Transaction Type"] == "10b Planned Sale - Common stock"
                ):
                    s_link["Transaction Type"] = "10b Planned Sale - Derivative"
                linked_sales.append(s_link)
                if price is not None:
                    try:
                        sale_value_sum += float(linked_needed) * float(price)
                    except Exception:
                        pass

                # mutate the original sale row into the residual piece (unlinked)
                s["Transacted Shares"] = -residual_abs
                if price is not None:
                    try:
                        s["Transaction Value Range ($)"] = float(residual_abs) * float(
                            price
                        )
                    except Exception:
                        pass
                # its label remains planned-common or non-plan sale as initially assigned
                remaining = 0
                break  # satisfied ex_est exactly

        # Compute match/tolerance
        linked_sum_shares = sum(
            abs(r.get("Transacted Shares") or 0) for r in linked_sales
        )
        match_delta = linked_sum_shares - ex_est
        max_total = max(abs(ex_est), abs(linked_sum_shares))
        tol_thresh = max(TOL_ABS, TOL_PCT * max_total) if max_total > 0 else 0
        if match_delta == 0:
            match_status, tol_used = "EXACT_MATCH", False
        elif abs(match_delta) <= tol_thresh:
            match_status, tol_used = "WITHIN_TOLERANCE", True
        else:
            match_status, tol_used = "MISMATCH", False

        aggregate_type = (
            "Exercise - Sale" if linked_sum_shares > 0 else "Exercise - Hold"
        )

        # Ranges and flags
        date_list = [r["Trade Date Range"] for r in ex_rows] + [
            r["Trade Date Range"] for r in linked_sales
        ]
        td_start = min(date_list) if date_list else ex_date
        td_end = max(date_list) if date_list else ex_date
        prices = [
            p
            for p in (
                [r.get("Price Range ($)") for r in ex_rows]
                + [r.get("Price Range ($)") for r in linked_sales]
            )
            if pd.notna(p)
        ]
        price_min = min(prices) if prices else None
        price_max = max(prices) if prices else None
        has_tax = any((tr["Trade Date Range"] >= ex_date) for tr in tax_rows)

        event_id = (
            f"{accession.replace('-', '')}-{ex_date.replace('-', '')}-{event_seq:02d}"
        )
        event_seq += 1

        # Build roll-up row
        rollup = {
            "RowTag": "ROLLUP",
            "EventID": event_id,
            "LinkRole": "",
            "Holder Name": (
                ex_rows[0]["Holder Name"] if ex_rows else rows[0]["Holder Name"]
            ),
            "Trade Date Range": (
                f"{td_start} - {td_end}" if td_start != td_end else td_start
            ),
            "Security Type": "Class A Common Stock",
            "Transacted Shares": -abs(linked_sum_shares) if linked_sum_shares else 0.0,
            "Transaction Value Range ($)": (
                sale_value_sum if linked_sum_shares else None
            ),
            "Transaction Type": aggregate_type,  # hyphen labels
            "SEC Transaction Code": "",
            "Price Range ($)": None,
            "End of Filing Shares": None,
            "% Change": None,
            "Filed Date": filed_iso,
            "Source": rows[0]["Source"],
            "accession": accession,
            "AD_Flag": "D",
            "AcqDisp": "D",
            "Rule 10b5-1 Plan": any(r.get("Rule 10b5-1 Plan") for r in linked_sales),
            "Tax-Related": has_tax,
            "_is_derivative": False,
            "_issuer_name": rows[0]["_issuer_name"],
            "_issuer_ticker": rows[0]["_issuer_ticker"],
            "_owner_role": rows[0]["_owner_role"],
            "AggregateType": aggregate_type,
            "Aggregate Shares": -abs(linked_sum_shares) if linked_sum_shares else 0.0,
            "PriceRange_Min": price_min,
            "PriceRange_Max": price_max,
            "TradeDate_Start": td_start,
            "TradeDate_End": td_end,
            "ExerciseShares_Est": ex_est,
            "ExerciseShares_Method": ex_method,
            "SoldNonTax_Sum": linked_sum_shares,
            "MatchDelta": match_delta,
            "MatchStatus": match_status,
            "ToleranceUsed": tol_used,
            "HasTaxRows": has_tax,
            "LinkedTxnCount": len(ex_rows) + len(linked_sales),
            "LinkedFootnotes": "",
        }
        out.append(rollup)

        # Append linked rows beneath roll-up and mark them as rolled
        for r in ex_rows + linked_sales:
            r["EventID"] = event_id
            r["RowTag"] = "SOURCE"
            r["RolledUp"] = True
            out.append(r)

    # Append remaining SOURCE rows (not rolled)
    for r in sorted(rows, key=lambda x: (x["Trade Date Range"], x["LinkRole"])):
        if r.get("RolledUp"):
            continue
        out.append(r)

    # Order within each event: roll-up first, then SOURCE (exercise > sale-common > tax > other)
    def order_key(row):
        row_order = 0 if row["RowTag"] == "ROLLUP" else 1
        link_order = {
            "exercise": 0,
            "sale-common": 1,
            "tax-sale-issuer": 2,
            "tax-sale-open": 3,
            "other": 9,
        }.get(row.get("LinkRole", ""), 9)
        sd = row.get("TradeDate_Start") or row["Trade Date Range"]
        return (
            row.get("EventID", ""),
            row_order,
            link_order,
            sd,
            row.get("accession", ""),
        )

    return sorted(out, key=order_key)


# ---------------- Orchestrator ---------------- #
def run(mode: str, outfile: str):
    filings = list_all_form4_filings(BLOCK_CIK10)
    total = len(filings)
    print(f"[Info] Enumerated {total} Form 4/4A filings for Block, Inc.")

    # For update mode, seed known accessions from existing CSV
    processed = set()
    if mode == "update" and os.path.exists(outfile):
        try:
            existing = pd.read_csv(outfile, dtype=str)
            if "accession" in existing.columns:
                processed = set(existing["accession"].dropna().astype(str).tolist())
            print(
                f"[Update] Existing rows: {len(existing)} | Known accessions: {len(processed)}"
            )
        except Exception:
            print("[Update] Could not read existing CSV — doing full rebuild")
            mode = "full"

    all_rows: List[Dict] = []
    skipped_no_xml = parse_errors = not_amrita = added = 0

    for i, f in enumerate(filings, 1):
        acc, prim = f["accession"], f["primary"]
        tag = f"[{i}/{total}] {acc}"
        if mode == "update" and acc in processed:
            print(tag, "... skip (already in CSV)")
            continue

        xml_text, from_cache = fetch_xml_for_accession(BLOCK_CIK10, acc, prim)
        if not xml_text:
            skipped_no_xml += 1
            print(tag, "... skip (no XML)")
            time.sleep(SLEEP_SEC)
            continue

        try:
            root = etree.fromstring(xml_text.encode("utf-8"))
            owners = [
                (ro.findtext(".//rptOwnerName") or "").strip()
                for ro in root.findall(".//reportingOwner")
            ]
            is_amrita = any(
                "amrita" in n.lower() and "ahuja" in n.lower() for n in owners
            )
            display = next((n for n in owners if "amrita" in n.lower()), "Amrita Ahuja")
            if not is_amrita:
                not_amrita += 1
                print(tag, "... skip (owner not Amrita)")
                time.sleep(SLEEP_SEC)
                continue

            src_rows, xml_txn_count = parse_transactions(
                xml_text, insider_display=display
            )

            doc_url = form4_doc_url(BLOCK_CIK10, acc)
            filed_iso = iso_date_from_mdy(f["filed_at"]) or (f["filed_at"] or "")

            # Fill per-row fields and count emitted SOURCE before roll-ups
            for r in src_rows:
                r["Filed Date"] = filed_iso
                r["Source"] = doc_url
                r["accession"] = acc

            emitted_source = len(src_rows)
            if emitted_source != xml_txn_count:
                print(
                    f"[WARN] {acc}: XML txns={xml_txn_count}, emitted={emitted_source}"
                )
                all_rows.append(
                    {
                        "RowTag": "SOURCE",
                        "EventID": "",
                        "LinkRole": "other",
                        "Holder Name": display,
                        "Trade Date Range": "",
                        "Security Type": "",
                        "Transacted Shares": None,
                        "Transaction Value Range ($)": None,
                        "Transaction Type": "XML PARSE WARNING",
                        "SEC Transaction Code": "",
                        "Price Range ($)": None,
                        "End of Filing Shares": None,
                        "% Change": None,
                        "Filed Date": filed_iso,
                        "Source": doc_url,
                        "accession": acc,
                        "AD_Flag": "",
                        "AcqDisp": "",
                        "Rule 10b5-1 Plan": False,
                        "Tax-Related": False,
                        "_is_derivative": False,
                        "_issuer_name": "",
                        "_issuer_ticker": "",
                        "_owner_role": "",
                        "_price_present": False,
                        "Plan Adoption Date": None,
                    }
                )

            # Build roll-ups and extend
            combined = build_events_and_rollups(
                src_rows, accession=acc, filed_iso=filed_iso
            )
            all_rows.extend(combined)
            added += len(combined)

        except Exception:
            parse_errors += 1
            print(tag, "... parse error")
            time.sleep(SLEEP_SEC)
            continue

        src = "cache" if from_cache else "web"
        print(
            tag,
            f"... +{len(src_rows)} SOURCE rows -> {len(combined)} total rows ({src})",
        )
        time.sleep(SLEEP_SEC)

    # Assemble DataFrame
    col_order = [
        "RowTag",
        "EventID",
        "LinkRole",
        "AggregateType",
        "Aggregate Shares",
        "PriceRange_Min",
        "PriceRange_Max",
        "TradeDate_Start",
        "TradeDate_End",
        "ExerciseShares_Est",
        "ExerciseShares_Method",
        "SoldNonTax_Sum",
        "MatchDelta",
        "MatchStatus",
        "ToleranceUsed",
        "HasTaxRows",
        "LinkedTxnCount",
        "LinkedFootnotes",
        "Holder Name",
        "Trade Date Range",
        "Security Type",
        "Transacted Shares",
        "Transaction Value Range ($)",
        "Transaction Type",
        "SEC Transaction Code",
        "Price Range ($)",
        "End of Filing Shares",
        "% Change",
        "Filed Date",
        "Source",
        "accession",
        "AcqDisp",
        "AD_Flag",
        "Rule 10b5-1 Plan",
        "Tax-Related",
        "_is_derivative",
        "_issuer_name",
        "_issuer_ticker",
        "_owner_role",
        "Plan Adoption Date",
    ]
    df = pd.DataFrame(all_rows)
    for c in col_order:
        if c not in df.columns:
            df[c] = None

    # Numeric coercion
    for c in [
        "Transacted Shares",
        "Transaction Value Range ($)",
        "Price Range ($)",
        "End of Filing Shares",
        "Aggregate Shares",
        "PriceRange_Min",
        "PriceRange_Max",
        "ExerciseShares_Est",
        "SoldNonTax_Sum",
        "MatchDelta",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Sort: newest first by TradeDate_Start / Trade Date Range; ROLLUP above SOURCE
    df["SortDate"] = df["TradeDate_Start"].fillna(df["Trade Date Range"])
    df["RowOrder"] = df["RowTag"].map({"ROLLUP": 0, "SOURCE": 1}).fillna(2)
    link_ord = {
        "exercise": 0,
        "sale-common": 1,
        "tax-sale-issuer": 2,
        "tax-sale-open": 3,
        "other": 9,
    }
    df["LinkOrder"] = df["LinkRole"].map(link_ord).fillna(9)
    df.sort_values(
        by=["SortDate", "EventID", "RowOrder", "LinkOrder", "accession"],
        ascending=[False, True, True, True, True],
        inplace=True,
    )

    df.to_csv(outfile, index=False, encoding="utf-8-sig")
    print(
        f"[Done] Wrote {len(df)} rows to {outfile} | added now: {added} | "
        f"skipped(no XML): {skipped_no_xml} | not Amrita: {not_amrita} | parse errors: {parse_errors}"
    )


# ---------------- CLI ---------------- #
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Amrita Ahuja (Block) — one-tab roll-ups (robust & split)"
    )
    ap.add_argument("--mode", choices=["full", "update"], required=True)
    ap.add_argument("--outfile", default="amrita_form4.csv")
    args = ap.parse_args()
    run(mode=args.mode, outfile=args.outfile)
