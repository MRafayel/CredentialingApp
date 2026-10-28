# State Medicaid exclusion lists: source survey

Surveyed 2026-10-16 and 2026-10-19 with at most six polite requests per site, from a
non-US network (Cloudflare reported the Yerevan data centre; egress IP 5.77.198.252). The owner
opens the "blocked" pages from the same city without trouble, so most blocks target the kind
of client (a script's TLS fingerprint, no JavaScript) rather than the location; a retry with
honest identification and full standard headers changed nothing except Minnesota. Sites that
dropped the connection at DNS/TCP level (no HTTP answer at all) may be network-level geo
filters and still need a browser check. Implemented so far: New York (connector `ny_omig`),
the federal OIG list (section at the end) and, since 2026-10-27, North Carolina, Maryland,
Vermont, North Dakota, Washington, Iowa, Indiana and Pennsylvania (connectors `nc_medicaid`,
`md_medicaid`, `vt_medicaid`, `nd_medicaid`, `wa_hca`, `ia_medicaid`, `in_fssa`,
`pa_medicheck`; first-sync figures under each state). Workbooks are read with openpyxl
(approved 2026-10-27). Sample files are not kept in the repository.

Nevada's OMIG publishes a directory of other states' lists at
https://www.omig.nv.gov/provider-enrollment--termination/provider-exclusions-sanctions-and-press-releases/provider-exclusions-sanctions-and-press-releases/
(38 links; 16 states beyond the owner's original list). It is a useful index, not a data source.

## What the survey means for the fetching architecture

1. **One request per poll in steady state is achievable for most sources.** The change signal
   differs per source, so a connector declares which one it uses:
   - `api-metadata`: ask the portal API for the file's hash or modified time (California).
   - `conditional-get`: `If-None-Match` and/or `If-Modified-Since` on a stable URL, expecting
     304 (North Carolina, Maryland, Montana, New Jersey, Iowa, Indiana, probably North Dakota).
     Some servers honour only one of the two headers; the connector records which.
   - `header-compare`: HEAD and compare ETag/Last-Modified ourselves, because the CDN ignores
     conditional requests (Washington).
   - `link-discovery`: the file name changes on every release, so re-read the landing page,
     extract the current link, and download only when the link changes (Georgia, Missouri,
     Wyoming, West Virginia, Nevada).
   - `content-hash`: no validators at all; download and hash the normalised rows (New York,
     District of Columbia, West Virginia).
2. **Every fetch is stored as a snapshot** (raw bytes, hash, headers, fetched-at) and the parsed
   rows are **diffed against the previous snapshot**. Removals matter: on several lists
   (Georgia for one) reinstatement is only visible as a row disappearing.
3. **Cadence:** weekly by default; daily only where the check is nearly free (California
   metadata, New Jersey conditional GET, New York's 0.5 MB TSV if daily freshness is wanted).
   Most lists change monthly. Honour `Crawl-delay` where stated.
4. **Access policy is a per-source attribute**, not an afterthought: `automated`,
   `robots-disallowed` (Nevada; California's API path), `blocked-for-clients` (WAF or bot
   manager), `javascript-portal` (Illinois, Vermont), `source-outage` (Mississippi today). Only
   `automated` sources get a fetcher; the rest become manual-verification steps with the
   official link until the owner decides otherwise. Scripts send a fixed standard browser
   User-Agent (owner decision 2026-10-26, configurable via `CREDENTIALING_USER_AGENT`); we still
   never bypass CAPTCHAs, interactive challenges or robots.txt. The brief allows *permitted*
   browser automation as a strategy; whether to use a real browser for the firewall-blocked
   sites is an owner decision, taken per source and recorded in its access policy.
5. **Failure outcomes are distinct results** (project invariant 3): a 403 from a WAF, a 404
   that hides a block (Ohio), a hosting outage (Mississippi) and "not on the list" must never
   look alike.
6. **No two lists share a schema.** Normalisation is per source into one internal record:
   name parts or organisation name, identifiers (NPI, state Medicaid ID, licence), action
   type, effective date, end/reinstatement date when given, reason when given, plus
   provenance. Expect: Excel date serials mixed with text dates, "N/A" placeholders, several
   identifiers in one cell separated by newlines or commas, credentials inside name fields,
   trailing spaces in categorical values, title rows above the header, and sheet names that
   change monthly (always take the first sheet).
7. **Columns that are not exclusions must never be matched:** North Carolina's `OWNERSHIP`
   and Nevada's "controlling interest" names list owners, not excluded parties.
8. **Sensitive columns: decided 2026-10-22, store them with safeguards.** Dates of birth (Georgia, reportedly Ohio) and
   street addresses (New Jersey, California, West Virginia, Minnesota, North Dakota, Illinois,
   DC) are published by the sources and are the strongest tools against same-name confusion on
   lists with little NPI coverage. Proposed: store them when a source publishes them, never
   log them, show them in the UI only as match evidence, and collect them from customers only
   as far as matching needs. The root rules (invariant 9) now say exactly this.
9. **NPI coverage is low on many lists** (New York 26%, Maryland 17%, Georgia 3%, Montana 26%,
   California only inside a mixed "Provider Number" column). Name matching will carry much of
   the weight, so the explainable match levels and human review of weak matches are essential.
10. **Parsing needs:** xlsx (most states), tab- or comma-delimited text (New York, New Jersey,
    California), HTML tables (Montana, DC), text PDFs with wrapped table cells (Wyoming, West
    Virginia, Nevada). PDFs need a coordinate-aware table parser; plain text extraction
    scrambles the columns. Library choices are dependency decisions for the owner.

## Summary

| State | Format | Stable file URL | Change signal | Stated frequency | NPI | Strategy | Status |
|---|---|---|---|---|---|---|---|
| NY | TSV / xlsx / HTML (9,141 rows) | Yes (`tabdelimited.aspx`) | None; xlsx rebuilt per request | Nightly refresh; "check every 30 days" | 26% | GET TSV daily or weekly, hash normalised rows | Automated |
| NJ | Tab-delimited, no header (4,088 rows) | Yes | `If-Modified-Since` → 304 (ETag unreliable) | Not stated | 29% | Daily conditional GET | Automated |
| CA | CSV via CKAN (23,526 rows, 3.4 MB) | Resource ID stable; file name monthly | CKAN `resource_show` gives MD5 hash and last_modified | Monthly | NPIs mixed into "Provider Number" | Daily metadata call; download on hash change | Automated, but robots.txt disallows `/api/` (owner to accept) |
| NC | xlsx (181 rows) | Yes (media alias) | ETag and Last-Modified → 304 | Monthly | 92% | Weekly conditional GET | Automated |
| MD | xlsx (1,622 rows) | Yes (SharePoint) | ETag and Last-Modified → 304 | "Monthly if needed" | 17% | Weekly conditional GET | Automated; sanction-code legend needed |
| MT | HTML table + xlsx (179 rows) | Landing yes; xlsx dated | 304 on the landing page itself | Not stated | 26% | Weekly conditional GET of landing page, parse table | Automated |
| WA | xlsx ×2 (234 rows; DSHS list dormant) | Yes | Headers present; CDN ignores conditionals | Not stated | 90% (mixed with ProviderOne IDs) | Weekly HEAD, compare headers, GET on change | Automated |
| IA | xlsx (1,318 rows) | Media URL stable; inner name monthly | `If-Modified-Since` → 304 | Monthly in practice | 58% (some are Medicaid IDs) | Weekly conditional GET | Automated |
| IN | xlsx (112 rows) + re-enrolment PDF | Yes | `If-None-Match` → 304 (IMS ignored) | Not stated (file July 2026) | ~95% | Weekly conditional GET | Automated; in scope as termination history (owner decision) |
| ND | xlsx (192 rows) | Yes | ETag and Last-Modified → 304 | Monthly by label | 26% (QSPs mostly none) | Weekly conditional GET | Automated; full street addresses on every row |
| GA | xlsx (1,377 rows) | No, date in path | Landing page link change; file 304 works | "At least monthly" | 3% | Weekly landing GET, download on new link | Automated; has DOB column |
| MO | xlsx (280 rows) | No, date in name | Landing page link change; `If-None-Match` → 304 | "Reviewed monthly" | ~100% | Weekly landing GET, download on new link | Automated |
| WY | PDF (6 pages, ~118 rows) | No, date in name and path | Landing page link change; file 304 works | Not stated | ~10%, free text | Weekly landing GET, PDF table parse | Automated once a PDF parser is approved |
| WV | PDF (13 pages, ~170 rows) | No, month in docname | Landing grid docname/modified; no validators | Monthly by name | 94% | Weekly landing GET, hash compare, PDF parse | Automated once a PDF parser is approved; terms unread |
| NV | PDF (36 pages, ~1,260 rows, 7 MB) | No, date in name | Landing page link change; file 304 works | "Periodically" | ~75% mixed with Medicaid IDs | Manual verification | robots.txt `Disallow: /` |
| IL | Dynamics 365 portal, Excel export; HFS page has no file | Token-gated endpoints | Page shows "Sanctions Last Updated" and counts | "Regularly"; readmitted providers are removed (no history) | Yes (column) | Manual, ask agency, or owner-approved automation of the export flow (3 deterministic requests, no CAPTCHA) | Anti-forgery token + session |
| VT | xlsx (69 rows) | Yes | ETag and Last-Modified → 304 | Monthly (title) | 77% | Weekly conditional GET | Automated (file URL supplied by owner) |
| DC | HTML tables (~100 rows) | Yes | Page validators are cache artefacts; hash tables | "As needed" | None | Weekly GET, hash | Wrong list: procurement debarment, not Medicaid |
| MA | xlsx + PDF (~300 rows) | No, dated `/doc/...-9302026/download` slug | ETag and Last-Modified present | Not stated (edition 9/30/2026) | Yes | Browser (even headless) finds the link; plain download works afterwards | Akamai blocks scripts; real Chrome passes |
| MI | xlsx (~4,110 rows) | No, `?rev=&hash=` changes | ETag and Last-Modified present | Not stated | Yes; also licence, city | Headed Chrome finds the link; plain download works afterwards | Akamai blocks scripts and headless Chrome |
| NH | xlsx (76 rows) | Yes (`provider-sanctions-list-update.xlsx`) | Last-Modified present | Not stated ("Updated 5/21/26") | Yes | Headed Chrome once to confirm the link; plain download works | Akamai blocks scripts and headless Chrome |
| OH | xlsx, sheets Individuals (1,867) and Organizations (255) | No, dated filename on a Cloudinary CDN | ETag and Last-Modified present | "Updated regularly" | Yes; **DOB on individuals**, full address on organizations | Headed Chrome finds the link (origin slow, ~17 s); plain download works | CloudFront WAF disguises its block as a 404; headless Chrome blocked |
| MN | xlsx ×2 (1,098 individuals, 433 organizations) | Yes (UCM `dDocName`) | None (`no-store`); dated filename in Content-Disposition | Monthly | None | Weekly HEAD, compare filename/length, GET and hash on change | Radware bot manager let the retry through; may challenge again. `Crawl-delay: 5` |
| KS | PDF (8 pages, ~200 rows) and xlsx (`DocumentCenter/View/516` and `/517`) | Yes | None: file generated per request, Last-Modified = now | "REVISED" date inside the file (Oct 21, 2026) | Yes; also KMAP provider # | Whole download must happen inside the browser (Cloudflare clearance is bound to the browser connection); hash the content | Cloudflare blocks scripts and headless Chrome |
| CO | xlsx (321 rows) + a separate reinstated-provider xlsx | No, edition date in filename | Last-Modified present, no ETag | Not stated | Yes | Headed Chrome finds the link; plain download works afterwards | AWS WAF blocks scripts and headless Chrome |
| AK | PDF (`AlaskaExcludedProviderList.pdf`, undated URL) | Probably | Unknown | Unknown | Unknown | Manual verification with the official link | Interactive Cloudflare Turnstile checkbox, headless and headed; we do not click it |
| MS | xlsx historically | Unknown | Unknown | Unknown | Unknown | Re-survey after outage | Site down (hosting outage) |

### States added from Nevada's directory (surveyed 2026-10-19)

| State | Format | Stable file URL | Change signal | Stated frequency | NPI | Strategy | Status |
|---|---|---|---|---|---|---|---|
| PA | CSV text (6,751 rows, 18 columns, 1.07 MB / 370 KB gzip) | Yes (`/Medchk/MedchkSearch/Medchk?target=_blank`) | None; body deterministic, hash it | Daily | 23% (all Luhn-valid) | Daily GET, hash, row diff | Automated |
| DE | PDF exported from Excel (37 pages, ~1,800 rows) | Yes (DMX download, EntryId 1671) | Last-Modified (ETag constant); 304 untested | Not stated; actively maintained | ~33%; also taxonomy, licence, DEA, reinstatement date | Conditional GET on the download URL, hash fallback, coordinate-aware PDF parse | Automated once a PDF parser is approved; ask DHSS for the Excel original |
| OR | SharePoint list behind a JavaScript table; exports disabled | List `/oha/HSD/OHP/Lists/Sanctions` | Unknown | Not stated | Unknown | Query the SharePoint list API (`_vti_bin/Lists.asmx` is robots-allowed) if the owner approves | Needs approval |
| CT | HTML table in an iframe (`portaldir.ct.gov/dss/ASPApps/list/actiondb.asp`) | Probably | Unknown | Unknown | Unknown | One GET of the iframe page, hash | F5 WAF 403 to scripts |
| TX | Info page only; search portal `oig.hhsc.state.tx.us/oigportal2/Exclusions` (download unconfirmed) | Unknown | Unknown | "Check monthly" | Unknown | Browser check of the portal for a download link | Portal: TCP timeout |
| ID | Laserfiche document (id 6158), format unknown | Probably | Unknown | Unknown | Unknown | Find the direct `ElectronicFile.aspx` link | Document host: TCP timeout |
| HI | PDF, dated filename (`...UPDATED-01.05.2023.pdf`) | No | Unknown | Unknown | Unknown | Landing GET to find the file, then conditional GET | Cloudflare 403 on the whole host |
| AL | Unknown | Unknown | Unknown | Unknown | Unknown | Browser check | TCP timeout (no HTTP answer) |
| AZ | Unknown | Unknown | Unknown | Unknown | Unknown | Browser check | Azure Application Gateway 403 |
| FL | Likely a search app (session id in URL) | No | Unknown | Unknown | Unknown | Browser check; probably per-provider lookup | Cloudflare 403 on the whole host |
| KY | Unknown (SharePoint page) | Unknown | Unknown | Unknown | Unknown | Browser check | Azure Front Door 403 |
| LA | Search app (`adverseactions.ldh.la.gov/SelSearch`) | Unknown | Unknown | Unknown | Unknown | Browser check for an export | Cloudflare 403 on all of la.gov |
| ME | Document viewer (`mhpviewer.aspx?FID=MEEX`) | Probably | Unknown | Unknown | Unknown | Browser check | TCP timeout |
| NE | Unknown (SharePoint page) | Unknown | Unknown | Unknown | Unknown | Browser check | DNS failure and TCP timeout |
| SC | Unknown | Unknown | Unknown | Unknown | Unknown | Browser check | CloudFront 403 |
| TN | Unknown | Unknown | Unknown | Unknown | Unknown | Browser check | DNS failure; AWS ELB 403 |
| NJ (Comptroller) | PDF expected | URL returns 404 | n/a | Unknown | Unknown | Owner to supply the current URL | Dead link; distinct from the Treasury file we have |

## Browser-automation experiment (2026-10-22)

Real Google Chrome 155 driven by Playwright 1.64, Chrome's own identity (default User-Agent,
`navigator.webdriver` left `true`, no stealth, no proxy), at most two page loads and one
download per site.

| State | Headless (UA contains "HeadlessChrome") | Visible window | Result |
|---|---|---|---|
| MA | passes | not needed | list downloaded |
| KS, CO, MI, NH, OH | blocked | **passes** | list downloaded from each |
| AK | Turnstile checkbox | Turnstile checkbox | manual verification |
| AZ, KY, HI, SC | instant WAF block | instant WAF block | likely network/IP rule; re-test behind the VPN |
| CT (iframe loaded directly) | F5 "Request Rejected" (status 200!) | F5 403 | retry through the parent page, else manual |

What production needs:
- **A visible window, not headless.** Five of the six working sites reject the "HeadlessChrome"
  token. Removing that token would hide the browser's identity, which we do not do; so the
  browser worker runs headed under a virtual display (Xvfb on Linux). Roughly 1 GB of memory
  per open browser, one browser at a time, 5–20 s per site.
- **The browser is for link discovery; most downloads can then go over plain HTTP** with the
  browser's cookie jar (CO, MA, MI, NH, OH). Kansas is the exception: Cloudflare binds its
  clearance to the browser connection, so the Kansas download happens inside the page.
- **Distinct blocked outcomes:** a fake 404 (OH), an F5 rejection page served with status 200
  (CT), an interactive Turnstile (AK). None may be read as "not found" or "page moved".
- File URLs change per edition on KS (generated per request), CO, MI, OH and MA; NH is stable.
  Use ETag/Last-Modified where present (MA, MI, OH, CO, NH); hash content for KS.
- Ohio's page asks users to re-fetch rather than keep local copies, and its individuals sheet
  carries DOB (stored under the DOB rule). Hawaii publishes a 2026 PDF
  (`...med-prov-excel-rein-list-2026/Med%20Prov%20Excl-Rein%20List-UPDATED-07.22.2026.pdf`),
  reachable only once the block is resolved.

## Cookie-reuse test on Kansas (2026-10-24)

Question: can a plain HTTP client reuse a browser-issued Cloudflare cookie instead of running
a browser? Findings, from one headed Chrome visit followed by httpx requests with an honest
User-Agent:
- Without cookies a script gets a 403 that is a JavaScript challenge ("Just a moment…",
  `cf-mitigated: challenge`). Cloudflare only issues a working cookie to a client that
  executes it, so obtaining the cookie from a script would mean solving the bot check: not
  done, by rule.
- With the browser's cookies the landing page and the xlsx download both return 200, with an
  honest non-browser User-Agent. Two Cloudflare cookies are involved: `__cf_bm` (bot
  management, 30-minute lifetime) and `cf_clearance` (set silently by Cloudflare's detection
  script seconds after the page loads; one-year lifetime; documented as bound to the visitor's
  IP). A fresh `__cf_bm` alone passes; `cf_clearance` alone does not; the full set still passed
  31 minutes later, after `__cf_bm` had expired, and Cloudflare issued a new `__cf_bm`. So a
  cookie jar could roll the session well past 30 minutes (untested beyond 31 minutes).
- The xlsx (`DocumentCenter/View/517/Termination-List-XLSX`, 26 KB) is generated per request:
  the filename carries a timestamp and `Last-Modified` is the request time, so change
  detection hashes the content; the workbook's internal modified date (docProps) is the usable
  "source updated at". Sheet `Terminations`, 213 rows, columns: Termination Date, D/B/A
  Business Name, Name, Provider Type, KMAP Provider #, NPI #, Comments.
- Decision: do not adopt cookie replay even though it could last longer than 30 minutes. The
  clearance is a pass issued to a browser that passed a bot check; carrying it in a different
  client for weeks is the grey zone we stay out of, and one headed browser run per weekly poll
  costs about ten seconds. Kansas is fetched inside the headed browser, once per poll. Any
  `cf-mitigated: challenge` response is a `blocked` outcome, never an empty list.

## Per-source notes

### New York (OMIG)
- Landing: https://apps.omig.ny.gov/exclusions/ex_formatted_list.aspx (6.6 MB server-rendered table). Index: https://omig.ny.gov/medicaid-fraud/medicaid-exclusions
- Downloads: `https://apps.omig.ny.gov/exclusions/tabdelimited.aspx` (text/plain, `ExclusionsAll.txt`, 537 KB), `exporttoexcel.aspx` (xlsx, 384 KB), `shortlist.aspx` (last 30 days, not fetched). Name search form at `ex_search.aspx`. The row "Verify" links confirm identity by SSN/FEIN: out of bounds for us.
- No cache headers (`Cache-Control: private`); conditional requests return 200. The xlsx zip timestamps differ per request, so hash the TSV or the parsed rows, never xlsx bytes.
- Columns: `PROVIDER_NAME, LICENSE_NUMBER, NPI_Number, PROVIDER_TYPE, EXCLUSION_EFFECTIVE_DATE`. No DOB, address, reason, end date.
- Quality: individuals and entities in one name field ("LAST FIRST M  MD", fixed-width padding); 420 rows embed AKA/DBA; trailing spaces in provider type; NPI on 2,350 rows, all 10 digits; licence on 6,072; dates MM/DD/YYYY 1982–2026; ASCII, CRLF, exactly 4 tabs per row.
- Access: F5 cookie set but not required; no challenge; robots.txt 404 on apps host; disclaimer limits use to "intended use", silent on automation.
- Implemented (2026-10-25): 9,161 rows, 9,145 distinct records, none skipped; 7,852 individuals and 1,293 organizations. Provider types are Title Case with about 400 distinct values; only a type ending in an entity noun ("Dental Practice", "Home Care Agency") and carrying no person-role word ("Clinic Employee", "Owner-Pharmacy", "Physician Practice") hints at an organization. Professional corporations of one person ("LAST FIRST MD PC", and the padded "LAST FIRST PC   MD") are read as that person, with the corporation name as an alias (89 rows).

### New Jersey (Treasury DORES medical debarment)
- Landing: https://www.nj.gov/treasury/revenue/debarment/debarsearch-medical.shtml
- Files: `https://www.nj.gov/treasury/treasfiles/debarment/Debarment-MEDICAL.txt` (tab-delimited, 521 KB, no header) and `Debarment-MEDICAL2.txt` (%-delimited). Search form posts to `/cgi-bin/treas/revenue/medical.pl`.
- `Last-Modified` and ETag present; `If-Modified-Since` alone → 304; sending both headers → 200 (ETag likely differs between load-balancer pool members). Use Last-Modified only.
- 22 documented columns (25 fields in the file, last 3 empty): firm name, individual name, vendor ID, firm address, NPI, individual address, category, action (DEBARMENT/SUSPENSION/DISQUALIFICATION), reason code A–K, department, agency, effective date, expiration date, permanent (Y/N). No DOB or licence.
- Quality: NPI on 1,186 rows; names "LAST, FIRST M. (LPN)"; one broken line; one shifted row; bad dates ("5/222024", year 4014, "DECEASED"); expiration holds "PERMANENT"/"RETIRED"/"DECEASED"; firms and individuals mixed, 83 rows have both; individual street addresses present.
- Access: Imperva CDN, no challenge; robots.txt disallows `/treasury/treasdocuments/` only.

### California (DHCS Suspended and Ineligible list, CKAN)
- Resource `48630a37-b5ba-4d3d-af54-e82b30e658a0`, package `8f70e05d-baa5-4296-a5ab-36318d530670` on https://data.chhs.ca.gov
- API: `/api/3/action/resource_show?id=<resource>` returns `url`, `last_modified`, `hash` (MD5 of the file, verified), `size`. `datastore_search` holds all 23,526 records. The CSV `url` changes monthly (`suspended-ineligible-list-september-2026.csv`) and 302-redirects to a presigned S3 URL valid 24 h; S3 honours conditional requests.
- `accrual_periodicity = monthly`; last upload 2026-10-12.
- Columns: `Last Name, First Name, Middle Name, A/K/A-Also Known As\nD/B/A-Doing Business as, Address(es), Provider Type, License Number, Provider Number, Date of Suspension, Active Period` + empty trailing column. No NPI column: `Provider Number` holds 10-digit NPI-like values on 1,874 rows, otherwise Medi-Cal numbers, sometimes several per cell.
- Quality: "N/A"/"n/a"/"NA" placeholders; ~1,280 entity rows with First Name "N/A"; 441 multi-line cells; dates M/D/YYYY; `Active Period` always "Indefinitely effective"; addresses present.
- Access: robots.txt disallows `/api/` and `/datastore/*` (the API is CKAN's official access path, but the owner must accept this); `/dataset/.../download/` allowed. Fallback: poll the resource page and read the download link. Contact: DHCSMandatorySuspensionsDesk@dhcs.ca.gov.

### North Carolina (NC Medicaid)
- Landing: https://medicaid.ncdhhs.gov/providers/excluded-providers
- File: `https://medicaid.ncdhhs.gov/nc-medicaid-state-excluded-provider-list/download?attachment` (stable alias; inner filename and sheet name carry the month). FAQ at `/excluded-provide-list-faqs/download`.
- ETag (Apache, Dynatrace suffix: compare as opaque string) and Last-Modified; both conditional headers → 304. "Updated monthly"; file lags about one month.
- Header on row 7: `EXCLUDED ENTITY | NPI/ATYPICAL ID (EXCLUDED) | EFFECTIVE DATE | REASON FOR EXCLUSION | CITY | STATE | ZIP CODE | OWNERSHIP`. Rows 1–6 are a preamble. `OWNERSHIP` names are not excluded parties.
- Quality: 167 NPIs, 11 "N/A", 3 eight-digit atypical IDs; 8 text dates among serials; reason codes STATE / OIG / FEDERAL and combinations; non-breaking spaces.
- Implemented (2026-10-27, `nc_medicaid`): 181 rows, 170 distinct records (an entity row is repeated once per owner), none skipped; 102 individuals, 68 organizations; NPI on 154, atypical ID on 3. People are "FIRST MIDDLE LAST" (last word is the last name, surname particles joined); maiden names appear in parentheses ("FIRST LAST (MAIDEN)") and become aliases; "(DBA: ...)" aliases; entity names without a business word ("... EAR NOSE AND THROAT", "... COUNTY MENTAL AND BEHAVIORAL") made "AND" and "COUNTY" business words. `OWNERSHIP` stays in the raw row only.

### Maryland (MDH)
- Landing: https://health.maryland.gov/mmcp/provider/Pages/sanctioned_list.aspx (SharePoint behind Cloudflare)
- File: `https://health.maryland.gov/mmcp/provider/Documents/sanctioned-list/State_of_MD_Sanctioned_Providers.xlsx` (244 KB, 1,622 rows). ETag `"{GUID},version"`; both conditional headers → 304. "Monthly if needed".
- Header row 1: `LAST NAME/ORGANIZATION | FIRST NAME | TYPE OF ENTITY/PROFESSION | SANCTION TYPE | NPI | LICENSE NO | TERMINATION/SANCTION DATE | ADDRESS | CITY/STATE/ZIP`.
- Quality: NPI on 280 rows (two cells hold two); licence on 456; entities have empty first name; inconsistent profession vocabulary; sanction codes HHS, LB, MDH, F, MFVVU, MFVVU-A, OIG, LC, LB/HHS with only LB explained; 7 rows lack a date.
- Access: robots.txt disallows `/_vti_bin/`, `/_layouts/`, `/_catalogs/` (do not use SharePoint APIs).
- Implemented (2026-10-27, `md_medicaid`): the data ends at the first blank row (row 1,617); below it the sheet itself carries the code legend and the list date ("10.08.2026"), which the first parse loaded as 7 bogus organizations until the reader stopped at the blank row. Legend, word for word: A = Abuse of adult/vulnerable individual; LB = Licensing Board discipline (Suspended, surrendered, revoked license); HHS = Sanctioned by U.S. Department of Health and Human Services, other state Medicaid program, or Medicare program; MDH = Other sanction by Maryland Department of Health Department; MFVVU = Medicaid Fraud And Vulnerable Victims Unit conviction; F = Other Medicaid fraud conviction. MFVVU-A, OIG and LC remain unexplained. The legend's meaning is stored as the record's reason. 1,615 rows, 1,606 distinct records, none skipped; 1,436 individuals, 170 organizations; NPI on 272 (4 cells hold ten digits that are no valid NPI), licence on 455. Codes: HHS 582, LB 420, MDH 270, F 216, MFVVU-A 69, MFVVU 46, blank 7. Some city cells lack the state ("Glen Burnie 21060").

### Montana (DPHHS)
- Landing: https://dphhs.mt.gov/montanahealthcareprograms/terminatedexcludedproviders holds the full HTML table (179 rows) and links a dated xlsx (`...Providers7.27.26.xlsx`).
- Both landing page and xlsx have ETag and Last-Modified and return 304. Server timestamps move on redeploys, so also compare hashes.
- Columns: provider ("Last, First"), healthcare profession, NPI, effective date, unnamed source column ("OIG Exclusion" 144, "State Termination" 21). NPI on 47 rows. Most rows mirror OIG.
- Page says the list "is not all-inclusive" and should not be the sole verification source.

### Washington (HCA)
- Landing: https://www.hca.wa.gov/billers-providers-partners/become-apple-health-provider/provider-termination-and-exclusion-list
- Files: `https://www.hca.wa.gov/assets/billers-and-providers/termination-exclusion.xlsx` (HCA, 28 KB, 234 rows) and `termination-exclusion-dshs.xlsx` (DSHS social services, 1 row, last edited 2022).
- ETag and Last-Modified present, but the CDN returns 200 to conditional requests: compare headers ourselves.
- HCA header on row 4: `Name | License # | NPI # or P1 # | Date of Exclusion | Action`. Names "Last, First CREDENTIAL"; 210 NPIs plus 34 ProviderOne IDs ("P1 - #"); 12 cells with several NPIs; Action is free text with typos mixing licence actions, OIG exclusion and termination.
- Implemented (2026-10-27, `wa_hca`, HEAD then GET): 234 rows and records, none skipped; 226 individuals, 8 organizations; NPI on 180, ProviderOne ID on 32, licence on 232. Credentials seen after names: ARNP, PA-C, PAC, LD, DN, MW, LM, SUDP, AuD, OT, AAC, ML, MHC, SW, Psy, "MD/". Ten "Name" cells hold several lines ("Last, First MD" then "dba: Clinic", or two business names then the owner): the first line is the party; how the other lines are read is below. Actions by keyword: suspension 80, sanction (licence surrendered/revoked/expired and others) 78, termination 56, exclusion 20. Some ten-digit identifiers (`52037...00`) fail the NPI check digit and stay raw.
- Related NPIs and parties (2026-10-28). Of the 12 identifier cells with several values, 10 hold two NPIs, one an NPI and a ProviderOne ID, one three ProviderOne IDs. Checked against NPPES: in every two-NPI cell one NPI is a person's (type 1) and the other that person's business's (type 2), in no reliable order (one row lists the business first, another the person first), so NPIs cannot be paired with names by position. The first NPI stays the record's `npi`, the others become `related_npis`. Name cells now split by kind: the first line is the party; a "dba" line or a line naming the same kind of party is an alias; a line naming the other kind (the owner on a business's row, the business on a person's row) is a related party. Re-sync with `--force`: 13 records changed (versioned), 221 unchanged; 10 with a related NPI, 8 with a related party, 3 with an alias.
- Related parties are flagged, not listed (2026-10-29). Washington flags the owner and workers of an excluded business, and the business of an excluded owner. A provider identified on a row by a related party's name (with a related NPI confirming it) gets the finding `related_party` and the verdict "Flagged"; by that name alone it stays a possible match for review.

### Iowa (HHS)
- Landing: https://hhs.iowa.gov/medicaid/provider-services/excluded-individuals-and-entities
- File: `https://hhs.iowa.gov/media/19531/download?inline` (Drupal media; inner filename carries the month, e.g. `External IME_Sanction_List_Sept2026_...xlsx`). Last-Modified, no ETag; `If-Modified-Since` → 304. Monthly in practice.
- Columns: `Enrollment Type, Specialty, NPI, Affiliated NPI, Last Name, First Name, Legal Business Name, Type of Sanction, Effective Date, Sanction End Date, Eligible to Reapply Date, Authority, State License Type, State License Number`.
- Quality: 1,318 rows; NPI filled on 761 (412 numeric, some not 10 digits; 344 shaped `X000000111`, Medicaid IDs); typos in enrollment type; end date mixes "Indefinite", "Federal Authority" and serials; one person can have several rows (state and federal).
- Implemented (2026-10-27, `ia_medicaid`): 1,318 rows, 1,317 distinct records, none skipped; 1,061 individuals, 256 organizations; NPI on 410, Medicaid ID on 347, licence on 470; end date on 170, of which 141 are already past. Sanction types: Termination 482, Participation Suspension for Overpayment 371, Participation Suspension 237, Federal Exclusion 225. Also seen: end dates "2 Years", "On Payment Plan"; one effective date "12/17/2025 - on payment plan" (counted unreadable).
- `Affiliated NPI` (2026-10-28): filled on 4 organization rows; 3 hold a valid NPI, stored as a related NPI (`related_npis`), and one a Medicaid ID, which stays raw. Re-sync: 3 records changed, 1,314 unchanged.

### Indiana (FSSA OMPP)
- Landing: https://www.in.gov/fssa/ompp/provider-information/termination-of-provider-participation-in-medicaid-and-chip
- Files: `https://www.in.gov/fssa/ompp/files/OMPP_Terminations.xlsx` (stable, 112 rows) and a re-enrolment PDF (`/dA/e4ba7227b8/Re-enrollments.pdf`). Weak ETag → 304; `If-Modified-Since` ignored.
- Header on row 2: `Provider Name | National Provider Identification (NPI) | Service Location Address | Termination Effective Date`. City/state only. Names include credentials; stray newlines; one cell with two NPIs.
- The state says this is "not an active or ongoing exclusionary list"; reinstatements only in the PDF.
- Implemented (2026-10-27, `in_fssa`, display name "Indiana Medicaid Provider Terminations"): 112 rows and records, none skipped; 76 individuals, 36 organizations; NPI on 105. Not in the survey: people are written first name first with the credential after a comma ("Rowan Castellane, DDS", "Devin Marlow, Podiatrist", "Sasha Okonkwo M.D."); one-word names are businesses; a provider with several practice locations has all of them in the address cell (newlines or runs of spaces), and only the first is stored as the address.
- The one cell with two NPIs (2026-10-28): the first is the record's NPI, the second a related NPI; that second NPI is itself on the OIG list as an organization. Re-sync: 1 record changed, 111 unchanged.

### North Dakota (HHS) — corrected source
- The owner's list pointed at Ohio's URL. Official page: https://www.hhs.nd.gov/healthcare/medicaid/provider/compliance/fraud-and-abuse
- File: `https://www.hhs.nd.gov/sites/default/files/documents/provider-exclusion-list.xlsx` (45 KB; link text carries the month). Strong ETag and Last-Modified; conditional GET → 304. Monthly by label.
- Sheet `ND Provider Exclusion List`: merged title row with the list date, header on row 2 (Excel table), 192 data rows. Columns: `Provider Name | Street Address | City | State | Zip | NPI | License Number | Provider Type | Exclusion Date | Reason for Exclusion`. Real Excel dates. Full street address on every row. No DOB.
- Quality: NPI on 50 rows (one is 9 digits, invalid) and `N/A` on 142; ZIP mixes numbers, text and ZIP+4; 5 rows with provider type `N/A`; trailing spaces in `Provider Type`; names "LAST, FIRST".
- QSPs: 105 of 192 rows are Qualified Service Providers; 84 have no NPI, none has a licence; 10 are organizations. The state verifies QSPs by name, DOB and SSN last four. We never collect SSN fragments, so a QSP without an NPI can reach at most a potential match on name plus address and goes to a person.
- Implemented (2026-10-27, `nd_medicaid`): 192 rows and records, none skipped; 177 individuals, 15 organizations; NPI on 49, licence on 54. Not in the survey: former names inside the name cell, "LAST (aka OTHER), FIRST" and "LAST (OTHER, OTHER), FIRST", stored as full-name aliases ("OTHER, FIRST"), and nicknames in parentheses.

### Georgia (DCH OIG)
- Landing: https://dch.georgia.gov/office-inspector-general/georgia-oig-exclusions-list ("updated at least monthly")
- File URL changes each release (`/document/document/...-october-5-2026/download`). Last-Modified, no ETag; `If-Modified-Since` → 304 on a given URL. Landing page validators change more often than the file.
- Four sheets, data on Sheet1, header on row 3: `LAST NAME, FIRST NAME, MIDDLE NAME, BUSINESS NAME, GENERAL, STATE, SANCDATE, NPI, DOB`.
- Quality: 1,377 rows; `SANCDATE` mostly integers shaped YYYYMMDD plus a few serials; NPI on 38 rows; DOB on 2 rows (sensitive); "recently added" shown only by cell highlighting; removal from the list is the only reinstatement signal.

### Missouri (MMAC)
- Landing: https://mmac.mo.gov/providers/provider-sanctions/ ("reviewed monthly"; terminations since 2/3/2011; "not all providers ... are on the list")
- File URL changes (`/wp-content/uploads/sanction-list-YYYY-MM-DD.xlsx`). ETag (Dynatrace suffix) → 304; `If-Modified-Since` alone → 200.
- Sheet "CURRENT", title row then header: `PROVIDER NAME, NPI, TERM DATE, LETTER DATE, PROV TYPE/SPECIALTY, LICENSE #, TERMINATION REASON`. 280 rows; NPI on nearly all; 14 text dates among serials; one row per provider type (duplicates); reasons include BAD DEBT, FRAUD, OIG EXCLUSION.

### Wyoming (WDH)
- Landing: https://health.wyo.gov/healthcarefin/medicaid/ (WordPress; link inside a toggle; `Crawl-delay: 10`)
- File URL changes (`/wp-content/uploads/2026/09/2026_09_01_-Wyoming-Medicaid-Exclusion-List.pdf`). ETag and Last-Modified → 304 on a given URL. No October file as of 2026-10-16.
- PDF: 6 landscape pages, text extractable, ~118 rows. Columns: Last Name, First Name, Business Name, Provider Type, Provider Number ("Prov # NPI"), City, State, Exclusion Date. Provider Number is free text mixing Medicaid IDs, "NPI 1...", licence numbers from several states, N/A. Cells wrap; aliases in parentheses.
- WordPress REST media API returns 403.

### West Virginia (BMS via Gainwell portal)
- Landing: https://www.wvmmis.com/SitePages/Medicaid%20Provider%20SanctionedExclusion (ASP.NET grid; download is a `__doPostBack` but the plain GET URL is derivable from the grid's docname)
- File: `https://www.wvmmis.com/SharepointDownload.aspx?parent=Medicaid+Provider+SanctionedExclusion&docname=WV%20Provider%20Exclusions%20and%20Terminations%20October%202026.pdf` (month in docname). No validators, `no-store`; conditional → 200. Grid shows the modified timestamp.
- PDF: 13 pages, text extractable, ~170 rows. Columns: Last Name, First Name, NPI, Address 1, Address 2, City, State, ZIP, Action Date, Excluded/Terminated, Reason. NPI on ~159. Heavy wrapping; mixed date formats including 2-digit years; prison addresses with inmate numbers.
- Footer links Gainwell site terms (unread).

### Nevada (OMIG)
- Landing: https://www.omig.nv.gov/provider-enrollment--termination/provider-exclusions-sanctions-and-press-releases/ ("updated periodically")
- File URL changes (`/siteassets/press-releases/master-exclusions-10.12.2026.pdf`, 7 MB, 36 pages, text). ETag and Last-Modified → 304 on a given URL.
- Columns: name, NPI, Medicaid provider flag, provider type codes, associated legal entity, controlling-interest persons, contract termination date, sanction tier, sanction period, period end date, OIG exclusion date, OIG reinstate date. ~1,260 rows; ~938 NPI-like numbers mixed with Medicaid IDs; entity repeated per controlling-interest person.
- **robots.txt is `Disallow: /`** for all but two named crawlers. Treat as manual verification unless Nevada OMIG grants permission.

### Illinois (HFS OIG sanctions portal)
- https://ilhfspartner3.dynamics365portals.us/sanctions/ is a Microsoft Power Pages site. Grid and "Export Data" need an anti-forgery token, an encrypted view configuration and session cookies. No OData feed. No CAPTCHA seen. Page server-renders "Sanctions Last Updated", "Active Sanctions Count" (3,273) and "Recent Additions Count".
- Columns from view metadata: Provider Name, License #, NPI, Provider Type, Affiliation, Action Date, Action Type (Barred, Exclusion, Suspended, Terminated, Voluntary Withdrawal), Address, City, State, Zip.
- Options: manual verification; ask HFS OIG (hfs.oigwebmaster@illinois.gov) for a feed; or owner-approved automation of the export flow. The landing page alone gives a cheap change signal.

### Vermont (DVHA / Gainwell portal)
- https://dvha.vermont.gov/providers/medicaid-excluded-providers links to `https://www.vtmedicaid.com/#/provEnrollExcluded` (AngularJS app). The owner located the file: `https://www.vtmedicaid.com/assets/provEnroll/excludedProvidersList/ExcludedProvidersList.xlsx` (65 KB, stable URL). Weak ETag and Last-Modified; conditional GET → 304. No robots.txt on the host.
- Sheet `Excluded Providers`: row 1 title "Vermont Monthly List of For Cause Exclusions - October 2026" (A1 holds a logo image, reads as `#VALUE!`), header on row 2, 69 data rows (3–71), footnotes and a key in rows 73–81.
- Columns: `VT Medicaid Provider ID`, `Provider NPI` (53 of 69), `Provider Full Name`, `Provider Type`, `Provider City`, `State`, a second `State` column that is really a reason code (07 Medicare/other-state exclusion, 22 sanctions, 19 contract terms, 08 fraud conviction), `Reason or "Cause" for Exclusion`, `Provider Exclusion Status Effective Date` (real dates), `Provider Exclusion Status End Date` (placeholder 2382-12-31 = open-ended), `Reinstatement Date` (empty). No DOB, street address or licence.
- Quality: names "LAST,FIRST M" with no space after the comma and heavy trailing padding; 12 organizations; one provider ID twice with different types; two NPIs under two provider IDs each.
- Implemented (2026-10-27, `vt_medicaid`): the last header reads "Reinstatement Date with Vermont Medicaid if Applicable" (the first parse failed on it, so the file was re-parsed offline, without a second download). 69 rows, 68 distinct records, none skipped; 56 individuals, 12 organizations; NPI on 52; every end date is the 2382-12-31 placeholder (stored as none). Counsellor and social-worker credentials (LCMHC, LICSW) follow the names.
- Related NPIs (2026-10-28): no `Provider NPI` cell holds two NPIs today (the "two NPIs" above are one NPI on two rows); should one appear, the first is the record's NPI and the others related NPIs. Re-sync: 68 unchanged.

### District of Columbia (OCP)
- https://ocp.dc.gov/page/excluded-parties-list is the **procurement debarment list** (DC Code § 2-359.07), not a Medicaid exclusion list. Four HTML tables (~100 rows), no NPI. Page validators are Drupal cache artefacts. The DC Medicaid (DHCF) source still needs to be identified.

### Minnesota (DHS MHCP)
- Landing: https://www.dhs.state.mn.us/main/idcplg?IdcService=GET_DYNAMIC_CONVERSION&RevisionSelectionMethod=LatestReleased&dDocName=DHS-295094 (Oracle WebCenter; Radware Bot Manager cookies; `Crawl-delay: 5`; robots.txt disallows `IdcService=DOC_INFO*` only).
- Files (stable by design, `RevisionSelectionMethod=LatestReleased`): `...idcplg?IdcService=GET_FILE&RevisionSelectionMethod=LatestReleased&Rendition=Primary&allowInterrupt=1&dDocName=DHS16_177448` (individuals, 64 KB, 1,098 rows) and `dDocName=DHS16_177447` (organizations, 38 KB, 433 rows).
- On 2026-10-16 both files redirected to `validate.perfdrive.com` (bot manager). On 2026-10-19, with full standard headers, both returned 200. Treat a perfdrive redirect as a `blocked` outcome, never "not found".
- Headers: `no-cache, no-store`, no Last-Modified, no ETag. Change signal: the dated filename in Content-Disposition (`Excluded Ind Providers 10-01-26.xlsx`) and Content-Length from a HEAD, then hash on download. Monthly.
- Individuals columns: `Provider type Description | Last name | First name | Middle name | Effective date of exclusion | Address line1 | Address line2 | City | State | Zip code | Exclusion status`. Text dates m/d/yyyy; address on 57 rows; 1,041 rows are Direct Support Workers; status values "Medicaid authority", "Medicare Medicaid exclusion", "Denied not eligible".
- Organizations columns: `Provider type description | Practice name | Effective date | Address line1/2 | City | State | Zip code | Exclusion status` (status in upper case; address on all rows).
- No NPI, licence, DOB, end date or reason beyond the status in either file.

### Pennsylvania (DHS Medicheck precluded providers)
- Info page: https://www.pa.gov/agencies/dhs/report-fraud/medicheck-list ("updated daily"; invites monthly screening; confirmation of a match uses SSN last four or FEIN via a VERIFY link, which is off-limits for us).
- App: `https://www.humanservices.dhs.pa.gov/Medchk/` (ASP.NET MVC behind Azure Application Gateway; no robots.txt). Search form posts to `/Medchk/MedchkSearch/ProviderSearch`; **"Download Text File" is a plain GET** of `https://www.humanservices.dhs.pa.gov/Medchk/MedchkSearch/Medchk?target=_blank` with no token or session.
- File: CSV, every value quoted, CRLF, 18 fields on every row, 6,751 data rows, 1.07 MB (370 KB gzip). No Last-Modified or ETag; conditional GET → 200; two downloads five minutes apart were byte-identical, so hashing the body works.
- Columns: `ProviderName, LicenseNumber, Status, BeginDate, EndDate, CAO, ListDate, IND_CHGD, DTE_CHANGE_LAST, NAM_LAST_PROVR, NAM_FIRST_PROVR, NAM_MIDDLE_PROVR, NAM_TITLE_PROVR, NAM_SUFFIX_PROVR, NAM_PROVR_ALT, NAM_BUSNS_MP, IDN_NPI, NBR_FEIN`. NPI on 1,554 rows (all Luhn-valid); licence on 4,418; FEIN on 207; county (`CAO`) instead of address; no DOB, no reason.
- Quality: it is a history table (981 name keys have several rows, up to 8); dates `MM/DD/YYYY 0:00:00`; an EndDate of year 2299; 466 blank statuses (454 of them with an EndDate); statuses Precluded, Terminated, Reinstated, Voluntary Withdrawal, Suspended, Permanent Preclusion and variants; inconsistent suffixes.
- Implemented (2026-10-27, `pa_medicheck`): 6,753 rows (1,070,349 bytes, UTF-8), 6,734 distinct records, none skipped; 6,454 individuals, 280 organizations; NPI on 1,554, licence on 4,400. Actions: preclusion 5,426, termination 717, other (blank status or withdrawal) 504, reinstatement 78, suspension 8. End date on 2,253 rows, about 1,800 of them in the past; placeholders in 2299 and one in 2229. `NAM_TITLE_PROVR` is the credential (RN, MD, LPN, PCA, RPh) and joins the suffix; `NAM_BUSNS_MP` can hold "a/k/a" aliases. Of the reinstated people, nearly every earlier row carries its own end date; one preclusion row without an end date precedes a reinstatement, so that person stays "listed" (the cautious reading).

### Delaware (DHSS DMAP)
- Landing: https://medicaid.dhss.delaware.gov/Default.aspx?TabId=2102 (DNN; first GET is a 302 cookie-support probe `AspxAutoDetectCookieSupport=1`, then 200). Link "Delaware Listing of Sanctioned Providers" → `https://medicaidpublications.dhss.delaware.gov/docs/Search?Command=Core_Download&EntryId=1671` → 302 → `https://medicaidpublications.dhss.delaware.gov/docs/DesktopModules/Bring2mind/DMX/API/Entries/Download?Command=Core_Download&EntryId=1671&language=en-US&PortalId=0&TabId=94` (PDF, 926 KB, no session needed). No robots.txt on either host.
- Headers: Last-Modified 2026-10-15; `ETag: "1671"` equals the EntryId and is useless for change detection; `Cache-Control: no-cache`. 304 untested (budget).
- PDF: 37 landscape pages exported from Excel ("Acrobat PDFMaker 26 for Excel"), text extractable, header repeated per page, ~1,800 rows. Columns: `Year | Sanctioned Provider Name | NPI # | Taxonomy # | License # | DEA # | OIG / Medicaid Sanction | Effective Date | Reinstated Date | Comments`. NPI on ~648 rows. No DOB or address.
- Quality: empty cells vanish from the text stream (parse by x-coordinate); credential suffixes in names; licences with state in parentheses; "N/A" ×422; `Reinstated Date` holds "Indefinite" ×316 and some dates earlier than the effective date; the sanction-source column is free text and many rows mirror PA DHS and OIG actions (deduplicate against OIG). Ask DHSS for the Excel original.

### Oregon (OHA)
- https://www.oregon.gov/oha/HSD/OHP/Pages/Provider-Enroll.aspx (SharePoint 2016 behind CloudFront). "Sanctioned Providers" is a JavaScript data-table web part over the SharePoint list `/oha/HSD/OHP/Lists/Sanctions`, view "Web"; Excel/CSV/PDF export buttons are disabled; no file link. robots.txt explicitly allows `/_vti_bin/Lists.asmx` and `Views.asmx`. Fetching the list through the SharePoint list API would return every row in one or two requests; it is the same public data the page loads, but not a published download, so it needs the owner's approval.

### Connecticut (DSS)
- https://portal.ct.gov/DSS/Quality-Assurance/Quality-Assurance-Administrative-Actions-List?language=en_US (Sitecore shell, allowed by robots.txt) embeds the data as an iframe: `https://portaldir.ct.gov/dss/ASPApps/list/actiondb.asp` (classic ASP, probably a single HTML table). portaldir.ct.gov answered 403 (F5 BIG-IP ASM) to scripts, including robots.txt.

### Texas (HHS OIG)
- https://oig.hhs.texas.gov/exclusions (Drupal, robots-allowed) is an information page ("check monthly") whose only data link is the search portal `https://oig.hhsc.state.tx.us/oigportal2/Exclusions`. The portal host dropped the connection (TCP timeout). Texas historically offered a full-list download there; needs a browser check.

### Idaho (DHW)
- https://healthandwelfare.idaho.gov/providers/idaho-medicaid-providers/information-medicaid-providers (Drupal, ETag and Last-Modified, robots-allowed) links "Medicaid Provider Exclusion List" to a Laserfiche viewer `https://publicdocuments.dhw.idaho.gov/WebLink/DocView.aspx?id=6158&dbid=0&repo=PUBLIC-DOCUMENTS`. The document host dropped the connection. Laserfiche normally serves a direct download at `/WebLink/ElectronicFile.aspx?docid=6158...` (unverified).

### Illinois, HFS page
- https://hfs.illinois.gov/oig/sanctionslist.html has no file. It describes the data set (terminations, barments, exclusions, suspensions, voluntary withdrawals; providers readmitted on petition are **removed**, so no history is kept; "updates this data set regularly") and links only to the Dynamics portal. Contact for a bulk export: hfs.oig.sanctions@illinois.gov.

### Blocked or unavailable to scripts (2026-10-16, unchanged on the honest retry of 2026-10-19)
- **Massachusetts** https://www.mass.gov/info-details/learn-about-suspended-or-excluded-masshealth-providers : WAF 403 to our client, including robots.txt.
- **Michigan** https://www.michigan.gov/mdhhs/doing-business/providers/providers/billingreimbursement/list-of-sanctioned-providers : Akamai 403, including robots.txt. Third party reports a single xlsx of ~4,243 entries.
- **New Hampshire** https://www.dhhs.nh.gov/programs-services/medicaid/medicaid-provider-relations : Akamai 403, including robots.txt.
- **Ohio** https://medicaid.ohio.gov/resources-for-providers/enrollment-and-support/provider-enrollment/provider-exclusion-and-suspension-list : CloudFront serves a static 404 page (unchanged since 2023) on every path including `/`; a block disguised as a 404. Search snippets say the file includes address and DOB.
- **Kansas** https://www.kdhe.ks.gov/172/Medicaid : Cloudflare 403 (CivicPlus), robots.txt too.
- **Colorado** https://hcpf.colorado.gov/provider-termination : CloudFront 403, robots.txt too.
- **Alaska** https://health.alaska.gov/en/office-of-the-commissioner/medicaid-program-integrity/ : Cloudflare JavaScript challenge (CAPTCHA-class) on the page and on the file `https://health.alaska.gov/Commissioner/Documents/PDF/AlaskaExcludedProviderList.pdf` (undated URL, from Nevada's directory).
- **Mississippi** https://medicaid.ms.gov/providers/provider-terminations/ : whole site down behind a "temporary site" notice (hosting outage) on 2026-10-16. Re-survey later.
- **Alabama** https://medicaid.alabama.gov/content/8.0_Fraud/8.7_Suspended_Providers.aspx : TCP connect timeout, no HTTP answer.
- **Arizona** https://www.azahcccs.gov/Fraud/Providers/actions.html : 403 from Azure Application Gateway, robots.txt too.
- **Florida** https://apps.ahca.myflorida.com/dm_web/doc_results_fo.aspx : Cloudflare 403 on the whole host; the URL shape (session id in path) suggests a search app.
- **Hawaii** https://medquest.hawaii.gov/.../Med%20Prov%20Excl-Rein%20List-UPDATED-01.05.2023.pdf : Cloudflare 403 on the whole host; dated filename.
- **Kentucky** https://www.chfs.ky.gov/agencies/dms/dpi/pe/Pages/terminated.aspx : 403 from Azure Front Door, robots.txt too.
- **Louisiana** https://adverseactions.ldh.la.gov/SelSearch : Cloudflare 403 for all of la.gov.
- **Maine** https://mainecare.maine.gov/mhpviewer.aspx?FID=MEEX : TCP connect timeout.
- **Nebraska** https://dhhs.ne.gov/Pages/Program-Integrity-Sanctioned-Providers.aspx : DNS failure (state name servers unreachable) and TCP timeout.
- **South Carolina** https://www.scdhhs.gov/fraud-waste-and-abuse : CloudFront 403 on robots.txt.
- **Tennessee** https://www.tn.gov/tenncare/fraud-and-abuse/program-integrity.html : DNS failure; 403 from the AWS load balancer.
- **New Jersey Comptroller** https://www.nj.gov/comptroller/doc/nj_debarment_list.pdf : 404 (dead link; Imperva CDN).

## Decisions taken (2026-10-22)

- Firewall-blocked sites: real Chrome under Playwright with its own identity, headed. Works for
  KS, CO, MA, MI, NH, OH. AK (Turnstile) is manual. AZ, KY, HI, SC, CT: re-test behind the VPN,
  then manual or ask the agency.
- California: CKAN API, resource page as fallback. Illinois: automate the token flow to the
  Excel export. DOB and addresses: stored with safeguards (see root rules, invariant 9).
- Cadence: weekly by default; daily for California metadata, New Jersey, New York, Pennsylvania.
- Alabama, Maine, Nebraska, Tennessee, Louisiana, Florida, the Texas portal and Idaho's document
  host fail in the owner's browser too: network-level filtering; the owner will use a VPN later.
- Indiana is in scope as termination history.
- 2026-10-26: the HTTP fetcher sends a fixed standard browser User-Agent instead of the product
  identity with a contact address (configurable via `CREDENTIALING_USER_AGENT`). A User-Agent
  alone does not pass fingerprint-based firewalls; CAPTCHAs, interactive challenges and
  robots.txt are still never bypassed.

## Still open

Nevada (robots.txt forbids automation); Oregon's SharePoint list API; the DC Medicaid source;
the New Jersey Comptroller URL; Maryland's codes MFVVU-A, OIG and LC (the file's own legend
explains the others) and whether its HHS code, which the legend defines more broadly than a
federal exclusion, should stay an exclusion; Delaware's Excel original; North Dakota QSPs
without NPI top out at "potential match"; the layout-aware PDF parser (the xlsx reader is
openpyxl); deduplication of state rows that mirror the federal OIG list; the ended-record
policy below (implemented as the default, owner to confirm).

## Ended records (default policy, 2026-10-27, owner to confirm)

Iowa, Pennsylvania, Vermont (placeholder only) and later New Jersey publish end dates. A
matched record has ended when its end date is before today, or when it is itself a
reinstatement row (Pennsylvania keeps one row per action). A check whose matched records have
all ended is still a found record, but its summary reads "Previously listed in <list>: ..."
with the end date and the provider's verdict is "needs review", not "listed"; one standing
record keeps the verdict "listed". End dates after the year 2100 are placeholders for
"open-ended" and stored as none.

## Federal: OIG LEIE (observed 2026-10-25)

- File: `https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv` ("Updated LEIE Database
  (CSV)"), linked from https://oig.hhs.gov/exclusions/leie-database-supplement-downloads/ (the
  old `exclusions_list.asp` answers 301 to it). Monthly supplement files (`YYMMexcl.csv`,
  `YYMMrein.csv`) exist; the full file is the one to use. robots.txt: 404.
- `200`, `text/csv`, 10,802,977 bytes, 84,093 rows, ASCII, CRLF, no BOM, 18 columns:
  `LASTNAME, FIRSTNAME, MIDNAME, BUSNAME, GENERAL, SPECIALTY, UPIN, NPI, DOB, ADDRESS, CITY,
  STATE, ZIP, EXCLTYPE, EXCLDATE, REINDATE, WAIVERDATE, WVRSTATE`. Cells with commas are quoted.
- Validators: `Last-Modified` only (no ETag). `If-Modified-Since` returns 304, so a poll costs
  one request until the next release (`conditional-get`).
- Cadence: monthly; each file includes "all actions taken during the prior month". Reinstated
  parties are removed, so a vanished row is the reinstatement signal (`REINDATE` is `0` on
  every row).
- Quality: dates `YYYYMMDD`, `0` as the empty value for NPI, REINDATE and WAIVERDATE; NPI on
  8,924 rows (all check-digit valid); DOB on 79,766 rows (sensitive, stored under the DOB
  rule); ZIP codes lost their leading zeros; 3,466 businesses in `BUSNAME`, never together with
  a person name; "NA" and "NULL" occur as real name values. 72 rows repeat another row's
  identity (same name, NPI, date), typically a second statutory basis on the same day.
- First sync: 84,093 rows parsed, none skipped, 84,021 distinct records stored; the next poll
  was a single 304.
