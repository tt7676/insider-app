---
name: datamule-form4
description: Query SEC insider transaction data (Forms 3, 4, 5) from Datamule's cloud database. Use this skill when working with insider trading data, insider ownership filings, or building applications that need to retrieve insider buy/sell transactions, holdings, and reporting owner information.
---

# Datamule Form 4 Skill

Query insider transaction data from SEC Forms 3, 4, and 5 using Datamule.

## Overview

**Forms covered:**
- Form 3: Initial ownership statement (filed within 10 days of becoming an insider)
- Form 4: Changes in ownership (filed within 2 business days of transaction)
- Form 5: Annual statement of changes (deferred transactions)

**Data available from 2004 to present.**

## Setup

```bash
pip install datamule
```

**Requires version 3.0.0 or higher.** Check version with `pip show datamule`.

Set API key via environment variable (recommended):
```bash
# In .env file
DATAMULE_API_KEY=your_api_key
```

## Recommended Workflow (Two-Step)

The paid API is useful for finding filings but has data gaps (prices show as 0, no footnotes). Use this two-step approach for complete data:

1. **Paid API** → Get list of accession numbers for an insider
2. **Submission tool** → Download and parse each filing with full data

### Why Two Steps?

| Field | Paid API | Submission Tool |
|-------|----------|-----------------|
| transactionPricePerShare | Often 0 | Actual price (e.g., $220.24) |
| Footnotes (10b5-1, tax, price ranges) | Not available | Full text |
| Transaction codes | Available | Available |

### Complete Example

```python
from datamule import Submission, format_accession
from dotenv import load_dotenv
import requests
import os

load_dotenv()
api_key = os.environ.get('DATAMULE_API_KEY')

# Step 1: Get ALL accession numbers using rptOwnerCik (not name)
# IMPORTANT: Loop through all pages to ensure completeness
all_accessions = []
page = 1

while True:
    response = requests.get(
        'https://api.datamule.xyz/insider-transactions',
        params={
            'table': 'reporting-owner',
            'rptOwnerCik': 1765417,  # Use CIK, not name
            'page': page,
            'pageSize': 100,
            'api_key': api_key
        }
    )
    data = response.json()
    records = data.get('data', [])

    if not records:
        break

    all_accessions.extend([r['accessionNumber'] for r in records])

    # Check if more pages exist
    total_pages = data.get('pagination', {}).get('totalPages', 1)
    if page >= total_pages:
        break
    page += 1

# Deduplicate
accession_numbers = list(set(all_accessions))

# Step 2: Download each filing using Submission tool (free, full data)
for acc_num in accession_numbers:
    acc_formatted = format_accession(str(acc_num), 'no-dash')
    url = f'https://sec-library.datamule.xyz/{acc_formatted}.sgml'

    sub = Submission(url=url)

    for doc in sub:
        if doc.type in ['3', '4', '5', '3/A', '4/A', '5/A']:
            data = doc.data  # Full parsed data with prices and footnotes
            # Process data...
            break
```

## Important Rules

### Use CIK, Not Name
- **Preferred:** `rptOwnerCik=1765417`
- **Avoid:** `rptOwnerName='Ahuja Amrita'` (name format issues)

### CIK Types
- `issuerCik` = the **company** (e.g., Block Inc = 1512673)
- `rptOwnerCik` = the **individual insider** (e.g., Amrita Ahuja = 1765417)

### Always Paginate
The API returns paginated results. Always loop through all pages to avoid missing filings.

### Cost Warning
The paid API charges ~$2.50 per 25,000 rows. Large companies (Apple has ~350,000 records) can be expensive.

**Testing Rule:** Limit test queries to 10 results. Larger queries require explicit permission.

The Submission download is **free** (no API credits).

## Available Tables (Paid API)

| Table Parameter | Description |
|-----------------|-------------|
| `metadata` | Filing-level metadata: issuer info, document types, reporting periods |
| `reporting-owner` | Insider details: CIK, name, address, relationship (director/officer/10% owner) |
| `non-derivative-transaction` | Common stock transactions: dates, codes, shares, prices |
| `non-derivative-holding` | Current common stock holdings (no transaction) |
| `derivative-transaction` | Options/warrants transactions: exercise prices, dates |
| `derivative-holding` | Current derivative positions |
| `signature` | Signatory names and dates |

## Transaction Codes

| Code | Meaning |
|------|---------|
| P | Purchase on exchange or from another person |
| S | Sale on exchange or to another person |
| A | Grant/award from company (e.g., stock option) |
| M | Exercise/conversion of derivative security |
| G | Gift |
| F | Tax withholding (payment of exercise price or tax) |
| D | Disposition to issuer |
| J | Other acquisition or disposition |

## Ownership Types

- `D`: Direct ownership
- `I`: Indirect ownership (e.g., held by spouse, trust, or entity)

## Known Data Issues (Paid API)

- `transactionPricePerShare` often shows 0 → **Use Submission tool instead**
- `deemedExecutionDate` shows `1899-11-30` as null placeholder
- No footnotes returned → **Use Submission tool instead**

## Resources

- [Datamule Documentation](https://john-friedman.github.io/datamule-python/)
- [Datamule Products](https://datamule.xyz/product)
- [SEC Forms 3, 4, 5 Guide](https://www.sec.gov/files/forms-3-4-5.pdf)
