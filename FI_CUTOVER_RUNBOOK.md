# Vuodevaatteet.fi Finland feed cutover runbook

Prepared on 2026-09-12 for Merchant Center account `5052901478`.

Status: production cutover completed on 2026-09-12. The active own source is
`Vuodevaatteet FI – Own Production`, ID `10728562355`. Its first fetch updated
126,725 offers, added six, recognized every attribute, and reported no file
errors. This document now also serves as the rollback and observation runbook.

## Decision

Use a direct, non-overlapping replacement of the current Simprosys Merchant API
source. The own file source must use the same Finnish language, `FI` feed label,
and stable `shopify_FI_<product>_<variant>` IDs. This keeps the existing Google
Ads feed-label targeting intact and avoids introducing a second production
label.

Google does not allow the same offer ID, content language, feed label, and
channel to be uploaded through multiple primary data sources. Therefore the own
production source must not be fetched while the Simprosys source is still
submitting the same Finnish offers.

References:

- [Product ID already used](https://support.google.com/merchants/answer/16425424?hl=en)
- [Create a product data source](https://support.google.com/merchants/answer/14990942?hl=en)
- [Use feed labels](https://support.google.com/merchants/answer/14994087?hl=en)

## Production source values

- Account: `5052901478`
- Name: `Vuodevaatteet FI – Own Production`
- Type: primary file source, scheduled fetch
- URL: `https://alexeysavolainen.github.io/finnmart-es-feed/vuodevaatteet-fi-production-candidate.xml`
- Country: Finland
- Language: Finnish (`fi`)
- Feed label: `FI`
- Marketing methods: Free listings and Shopping ads
- Fetch schedule: daily, after the GitHub build has completed
- Authentication: none

The source label and language are immutable after source creation. If either is
entered incorrectly, do not fetch the file; recreate the source correctly.

## Hard preflight gates

Proceed only when all checks pass immediately before cutover:

1. The latest GitHub Actions build is successful.
2. XML HTTP status is 200 and the published checksum matches its summary.
3. XML parses successfully, all IDs are unique, and validated item count is at
   least 120,000.
4. Candidate count differs from the current GMC count by no more than 2%.
5. Candidate links use `https://vuodevaatteet.fi/`, not `/es/`.
6. `custom_label_0..4` are read only from Shopify variants.
7. The current source settings, marketing methods, counts, and diagnostics are
   captured for rollback.

Baseline candidate validated on 2026-09-12: 126,725 unique offers. Current GMC
comparison: 126,720 offers, a difference of five (0.004%).

## Cutover sequence

This sequence is one maintenance operation. Do not leave it half-complete.

1. Run the full feed workflow once more and pass every preflight gate.
2. Pause all edits to feed settings during the maintenance window.
3. Stop the Finland export/synchronization in Simprosys so it cannot recreate or
   update the Merchant API source.
4. Remove the old `Simprosys Feed (Merchant API)` primary source, ID
   `10654636924`, from the Finnish Merchant Center subaccount.
5. Create `Vuodevaatteet FI – Own Production` using the exact values above.
6. Trigger the first fetch immediately.
7. Confirm that the file is fetched and parsed without source-level errors.
8. Compare received offer count to the preflight count. Do not evaluate only the
   number of currently approved items while Google is still processing.
9. Check at least one NovaEngel, Royal Textile, VidaXL, and Other-supplier offer:
   title, link, image, availability, price, sale price, GTIN/MPN, weight, Google
   category when supplied, and all five custom labels.
10. Confirm Shopping ads and free listings remain selected and Google Ads still
    targets feed label `FI`.

## Stop and rollback conditions

Rollback immediately if the file cannot be fetched, XML has a source-level
error, fewer than 120,000 offers are received, links point to the wrong market,
prices/currency are wrong, labels are product-level, or a duplicate-source error
appears.

Rollback procedure:

1. Stop scheduled fetches for the own source.
2. Re-enable the Finland synchronization in Simprosys.
3. Confirm that its Merchant API source and offer count return.
4. Keep the own source from fetching until the cause is corrected.

Do not cancel the Simprosys subscription during the observation period.

## Observation and cancellation

Monitor source processing, diagnostics, price and availability freshness, offer
count, clicks, and spend for at least 72 hours. Google notes that some diagnostic
changes can take up to 72 hours to appear. Cancel Simprosys only after the own
source has completed at least three successful daily updates and the Finnish
campaigns remain healthy.

## Known non-blocking data gaps

- 39,133 offers have no explicit Google product category. Google can classify
  these automatically; the generator does not guess unrelated categories.
- 10,629 offers use the explicitly approved 1 kg fallback.
- Missing labels 1, 2, 3, or 4 reflect genuinely empty variant metafields and
  are not filled from the parent product.
- 676 unusable product/variant rows lack a title, handle, or image; four variants
  have no positive price. They are excluded rather than sending invalid offers.
