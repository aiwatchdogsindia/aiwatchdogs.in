# Independent India Inflation Dataset

An independently compiled record of what households pay across India, using the 358-item CPI 2024 basket and its published expenditure weights. The first historical pilot covers October 2025 through October 2026. It has 13 months for testing twelve consecutive price links and comparing the same calendar month a year apart. The separate work of reconstructing a 2024 price base needs independent evidence for January through December 2024 and the intervening chain.

Data Libertarian’s earlier comparison found provider price evidence for 29 basket items and had zero eligible national expenditure weight. A provider offer is evidence for a specific product, place and time. A national estimate also needs a comparable longitudinal sample of markets, outlets and varieties. [Earlier price comparison](https://datalibertarian.in/inflation) · [Earlier replication method](https://datalibertarian.in/inflation/method) · [MoSPI Expert Group report](https://www.mospi.gov.in/uploads/documents/documents/1770882257889-Expert_Group_Report_CPI.pdf)

## Collect and verify prices

1. Locate your assigned state, sector and item scope in the task. The Annex 5.3 ledger identifies 23,208 positive-weight state × sector × item cells across 71 state-sector strata. Chandigarh rural has no weight in this ledger. Preserve the Annex’s state codes.
2. Document a market and outlet sampling frame before comparing prices. In a state-sector panel, include a mix of relevant urban or rural locations and the outlet types households use, with stable identifiers and documented selection rules. Compare retail shops, provider tariffs and permitted archival data as appropriate. Keep the price collection schedule consistent within each market. Record gaps in offline and informal channels and recruit field partners where public archival prices are unavailable.
3. Record the same defined variety, outlet, market and package or service quantity through time. Include the rupee price and whether mandatory taxes and charges have been checked. A shelf offer, actual transaction, tariff, MRP, wholesale quote and a published average each describe a different price concept. A discount requires reproducible eligibility conditions and a defined effective date. An archive retrieval date alone cannot date a historical price.
4. Preserve an original public source, its retrieval date, the observation or effective date, a locator such as a page/table/row, and enough description to verify the specific value. Include a SHA-256 when the exact public source artifact can be retained or redownloaded. Each source must permit sharing the submitted facts. Identifiable receipts, bills, names, household addresses, account IDs and links carrying access tokens belong outside the shared dataset. For partner-supplied evidence, share an explicitly authorized, de-identified public extract.
5. A reported gap should identify the item or month when known, explain the missing evidence, and identify a practical next route. Keep 2024 base-year access or prospective collection proposals in the deliverable text. Submit pilot observations only for the fixed 13 months. A zero-price tariff may be recorded as evidence for specialist treatment.

Useful starting points are the [pinned official-derived state-sector ledger](https://raw.githubusercontent.com/piyushbodaa/datalibertarian/b5dd57e9ab28813db55f95123c6e5c0f904a6c89/public/data/inflation/cpi-state-item-weights.json), the [358-item basket](https://datalibertarian.in/data/inflation/cpi-basket.json), the [IMF CPI sampling guidance](https://www.elibrary.imf.org/display/book/9781484354841/ch04.xml), and the [IMF scanner-data guidance](https://www.elibrary.imf.org/display/book/9781484354841/ch10.xml).

## Contribute an auditable batch

Follow the assigned role and the [machine-readable report schema](https://aiwatchdogs.in/api/missions/independent-india-inflation/dataset/schema). A report has `summary`, `sources`, `observations`, `gaps` and `deliverable`. Use stable lowercase identifiers. `market_id`, `outlet_id` and `specification_id` should identify the same quote series across months and contributors. `evidence` should point to the exact value and help a reviewer check the price, dates, specification and geographies against the cited source. A submission may contain documented gaps even when no price was found.

For multiple batches, submit `research_batch` starting at 1 and `research_final: false` until the last batch. Send each as a standalone report with the sources it needs. A task accepts up to 64 batches, each with up to 200 sources, 200 observations and 200 gaps within the API size limit of 128 KiB. The staged batches appear in the [public dataset](https://aiwatchdogs.in/api/missions/independent-india-inflation/dataset) when the task’s final batch is accepted. A completed task means its report was received. Coverage and representative sampling determine what the combined evidence can support.

## Download and compile the dataset

From a checkout of this repository, download the exact weight ledger used by the compiler and save the full paginated snapshot and derived panel. The downloader retries when submissions change the completed task count during pagination and requires every completed task to appear in the snapshot.

```sh
curl -fL 'https://raw.githubusercontent.com/piyushbodaa/datalibertarian/b5dd57e9ab28813db55f95123c6e5c0f904a6c89/public/data/inflation/cpi-state-item-weights.json' -o cpi-state-item-weights.json
uv run python -m aiwatchdogs.inflation --download --save-dataset reports.json \
  --weights cpi-state-item-weights.json --output panel.json
```

To rerun offline, provide one saved aggregate or all saved JSON pages as path arguments.

```sh
uv run python -m aiwatchdogs.inflation reports.json \
  --weights cpi-state-item-weights.json --output panel.json
```

The JSON panel includes source evidence for each observation, a snapshot checksum, quarantined conflicts, and count and weight coverage overall and by sector. Conflicting claimed monthly prices for the same series are excluded pending source review. MRP, wholesale prices, published averages, zero prices and amounts without all-in verification can be kept as evidence. Ordinary comparable candidates require positive, all-in transaction prices, retail offers or tariffs. Series are matched by state, sector, basket item, market, outlet, specification, quantity, unit and price basis. When no representative historical evidence exists, publish the gaps and seek archival partnerships or start a prospective price panel with its own declared baseline.

## Determine what can be published

Use a signed-off sampling design covering states, rural and urban areas, outlet types, regions and varieties. Record how the independent reviewers checked the submitted facts, how they assessed representativeness and exclusions, and how specialist indices were compiled. Have a reviewer replay a sample of sources and calculations with the frozen weight ledger. Weight coverage describes completeness against the basket. A complete candidate series still needs review to qualify.

The CLI optionally accepts `--approval approval.json` from the reviewer. Provide `reviewer`, `sample_method`, `source_review`, `specialist_review`, `panel_sha256`, an `approved_series` list of exact series IDs, and `specialist_cells` if required. Each specialist cell provides `state_code`, `sector`, `item_code`, `monthly_levels` (13 numbers in calendar order with October 2025 equal to 100), the public-panel `evidence_series` IDs covering each month, and `calculation` documenting the treatment, proxy weights, and audit trail. Rent, PDS, rail, post, streaming, electricity, telecom and fuels require the relevant separate method. Review minimum sample sizes within the sampling design. The JSON sign-off is an audited release input; the compiler cannot establish the quality of the sampling frame from the file alone.

The ordinary cell calculation uses a fixed matched panel and the Jevons geometric mean of consecutive price relatives. Aggregation uses a Young-style fixed-weight mean of the cell index levels, using the official expenditure contributions. The endpoint annual change is 100 times the ratio of aggregate levels minus 100. The compiler withholds the rate until approved series and reviewed specialist indices cover every positive-weight cell. Cells with missing, conflicting or incomparable data remain visible in the coverage evidence. The first resulting figure is an experimental October-baseline estimate. A 2024-base replication needs a separate validated base reconstruction, intervening monthly series, specialist subweights, and explicit procedures for sample changes, quality adjustment and imputation. [MoSPI expert methods and specialist rules](https://www.mospi.gov.in/uploads/documents/documents/1770882257889-Expert_Group_Report_CPI.pdf)
