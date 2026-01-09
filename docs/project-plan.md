\# Insider Trading App — Project Plan



\## Overview



\*\*What:\*\* Web app for investors to search an insider and see all their historic transactions alongside ownership data from proxy statements.



\*\*Who:\*\* Internal team of 5 at Decade Partners (MVP), then commercialise later with auth/payments.



\*\*Data source:\*\* Datamule API



---



\## Phase 1: Skill Creation

\- \[ ] Build Datamule skill from their documentation

\- \[ ] Document Form 4 (insider transactions) API patterns

\- \[ ] Placeholder section for proxy API \*(awaiting custom product from Datamule provider — expected ~1 week)\*



---



\## Phase 2: Discovery

\- \[ ] Get Datamule subscription + API key

\- \[ ] Test Form 4 queries, see actual data structure

\- \[ ] Refine skill based on real usage

\- \[ ] Build out bespoke categorisation logic rules based on patterns in the data

&nbsp; - Rules will be modular/separate in the repo so they can be updated as new edge cases emerge



---



\## Phase 3: High-Level Product Brief

\- \[ ] Document what the app does

\- \[ ] User flow (search insider → see results)

\- \[ ] Data fields required

\- \[ ] Categorisation logic framework

\- \[ ] Proxy section as placeholder with requirements



---



\## Phase 4: CLAUDE.md

\- \[ ] Project context

\- \[ ] Tech stack decisions

\- \[ ] Folder structure

\- \[ ] Coding conventions

\- \[ ] Based on Product Brief



---



\## Phase 5: Build MVP

\- \[ ] Form 4 functionality first

\- \[ ] Web dashboard interface

\- \[ ] Apply categorisation logic

\- \[ ] Map data to template \*(deferred from Phase 2)\*

\- \[ ] Brief and CLAUDE.md refined as we build



---



\## Phase 6: Add Proxy Data

\- \[ ] Update skill when provider delivers proxy API

\- \[ ] Integrate proxy ownership data into app

\- \[ ] Update Brief and CLAUDE.md accordingly



---



\## Notes



\- Product Brief and CLAUDE.md are living documents — will be refined throughout the build

\- Categorisation rules kept as separate section of repo for easy updates

\- Auth/payments/commercialisation is out of scope for MVP



