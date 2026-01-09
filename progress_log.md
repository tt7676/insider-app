# Progress Log

## Next Steps

- [ ] Investigate 8 remaining CAPIQ comparison issues (5 data differences + 3 missing values)
- [ ] Fix 15 "missing in our data" filings (date format mismatch)

---

-----
## 2026-01-09 (Session 4)

**Implemented: Issuer (Company) Filtering**

Problem: Data included ALL companies an insider worked at (e.g., Amrita had both Block/Square AND Airbnb). User only wants data for a specific company.

**Solution:**
- Added `--issuer-cik` as a **required** parameter
- Filter applied after parsing, before rollup building
- CIKs normalized (SEC format has leading zeros: `0001512673`)

**Auto-generated filenames:**
- Format: `{companyName}_{insiderName}_{DD.MM.YY}.csv`
- Example: `SquareInc._AhujaAmrita_09.01.26.csv`

**Usage:**
```bash
python main.py --cik 1765417 --issuer-cik 1512673
# Output: SquareInc._AhujaAmrita_09.01.26.csv
```

**Test results:**
- 298 transactions parsed from 92 filings (all companies)
- Filtered to 293 transactions for Square/Block (CIK 1512673)
- Airbnb data (CIK 1559720) successfully excluded

**CAPIQ Comparison - Re-run with Block-only data:**
```
Total filings compared:    121
Perfect matches:           73
Mismatches:                24
Missing in our data:       15
Missing in CAPIQ:          9

RESULT: 73 perfect matches, 8 to investigate, 9 non-issues
```

**Files updated:**
- `main.py` - Added `--issuer-cik` required param, filter logic, auto-filename generation
- `testing capiq data/block_amrita/comparison_report.txt` - Regenerated with filtered data

---

-----
## 2026-01-09 (Session 3)

**CAPIQ Comparison - Current State:**
- 73 perfect matches (up from 58)
- 24 mismatches remaining

**Key finding:** Removed Table 2 filter entirely. CAPIQ shows both Table 1 and Table 2 rows (acquisitions AND disposals). Our earlier assumption that CAPIQ only shows Table 1 was wrong.

**Remaining issues to investigate (parked for next session):**

| Priority | Category | Count | Description |
|----------|----------|-------|-------------|
| High | Missing in ours | 3 | CAPIQ has data we don't (42031, -3397, -1973) |
| High | Data differences | 5 | We show different values than CAPIQ |
| Medium | Extra in ours | 7 | We show Table 2 disposals CAPIQ doesn't |
| Medium | Filing date mismatches | 15 | CAPIQ date ranges vs our single dates |
| Low | CAPIQ rollups | 9 | Non-issue - presentation difference |
| Low | Missing in CAPIQ | 14 | Filings we have that CAPIQ doesn't |

**Files updated:**
- `compare_capiq.py` - Removed Table 2 filter, improved report bucketing
- `src/exporter.py` - Added sort by filedDate desc, grouped by accessionNumber

-----

-----
## 2026-01-08 (Session 2)

Built CAPIQ comparison tool and validated against Amrita's data.

**Final comparison results:**
- 58 exact matches
- 39 mismatches total:
  - 27 are CAPIQ rollups (NOT A PROBLEM)
  - 12 are genuine differences (TO INVESTIGATE)
- 15 filings missing in our data (date format issues)
- 14 filings missing in CAPIQ

**Improvements made to `compare_capiq.py`:**
1. Filter out Table 2 derivative rows (CAPIQ doesn't show these)
2. Detect and flag likely CAPIQ rollup values in mismatches

**Files updated:**
- `compare_capiq.py` - Added Table 2 filter and rollup detection
- `testing capiq data/block_amrita/` - Contains Amrita comparison data

-----

-----
## 2026-01-08

Merged the old functionality of the SEC tool into the Datamule pipeline - it's now working.

Fixed a bug for Amrita where she sold more shares than the amount of options she exercised. The fix now correctly:
- Splits sales at the exercise threshold
- Creates SYNTHETIC rows for the split portions
- Groups excess sales into an "Automatic Disposition" rollup

Generated a new `amrita_ahuja.csv` with all updates applied.
-----
