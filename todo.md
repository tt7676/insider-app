# Todo List

## Pending Tasks

### CAPIQ Comparison - Remaining Issues (8 to investigate)

| Priority | Category | Count | Description |
|----------|----------|-------|-------------|
| High | Data differences | 5 | We show different values than CAPIQ |
| High | Missing in ours | 3 | CAPIQ has data we don't (42031, -3397, -1973) |

### Other Tasks

- [ ] Investigate 15 "missing in our data" filings (date format mismatch between CAPIQ and our data)

---

## Completed Tasks

- [x] Add issuer (company) filtering with `--issuer-cik` parameter
- [x] Auto-generate filenames: `{company}_{insider}_{DD.MM.YY}.csv`
- [x] Sort CSV output by filedDate desc, grouped by accessionNumber
- [x] Build CAPIQ comparison tool with categorized mismatch bucketing
- [x] Remove Table 2 filter (CAPIQ shows both tables)
- [x] Build Form 4 tool natively for Datamule
- [x] Add SEC filing URL to transaction rows
- [x] Fix roll-up display order - ROLLUP rows sit on top of their SOURCE rows
- [x] Add Form 4 table source field - `secTable` shows "Table 1" or "Table 2"
