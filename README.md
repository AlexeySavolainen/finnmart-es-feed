# Finnmart Spain Merchant feed

Automated full-product feed for the Finnmart Spain market.

- Exact Shopify Market catalog: `Finnmart EU – NovaEngel + Royal Textile`
- NovaEngel and Royal Textile products only
- Spanish Shopify landing pages only
- Stable `shopify_ES_` offer IDs
- Spain shipping submitted per offer: `Entrega estándar (3–5 días laborables)`
- Shopify weight determines the matching `11.50 EUR` or `13.10 EUR` rate
- Invalid supplier weights use the approved 1 kg fallback
- Products without a required image are reported and excluded
- No dependency on the Finland feed or Simprosys

Public feed endpoint after deployment:

`/finnmart-es.xml`

Machine-readable validation report:

`/finnmart-es-summary.json`

Ireland English feed endpoint:

`/finnmart-ie.xml`

Ireland validation report:

`/finnmart-ie-summary.json`

France French feed endpoint:

`/finnmart-fr.xml`

France validation report:

`/finnmart-fr-summary.json`

Finland replacement pilot (100 Shopify products, isolated from live ads):

`/vuodevaatteet-fi-pilot.xml`

Pilot validation and field-coverage report:

`/vuodevaatteet-fi-pilot-summary.json`

Google custom labels 0–4 are read at variant level and never inherited from the
parent Shopify product.

Full Finland production candidate (all active and published Shopify products):

`/vuodevaatteet-fi-production-candidate.xml`

Full field-coverage and validation report:

`/vuodevaatteet-fi-production-candidate-summary.json`

The full candidate uses stable `shopify_FI_` offer IDs and is intentionally not
connected to Merchant Center automatically. It is generated with Shopify Bulk
GraphQL and written/validated as a stream so the complete catalog does not need
to fit in memory.

The current Simprosys/GMC field audit and label findings are documented in
`FI_FEED_FIELD_AUDIT.md`.

The guarded, non-overlapping migration procedure is documented in
`FI_CUTOVER_RUNBOOK.md`. The live Simprosys source must not overlap with the own
production source under the same Finnish language and `FI` feed label.

## Full Market synchronization

`sync_feed.py` reads only Shopify MarketCatalog
`gid://shopify/MarketCatalog/166288720196`, requires the exact catalog title,
allows only NovaEngel and Royal Textile tags, requires reviewed Spanish
translations, and exports the exact catalog with Shopify Bulk GraphQL. It then
validates unique Spanish offer IDs, localized landing pages, required fields,
shipping details, supplier coverage, and safety thresholds before publishing.

The scheduled GitHub Actions workflow builds the full Spain, Ireland and France feeds
daily and deploys the
`public` directory to GitHub Pages without committing generated XML files to Git
history. The required repository secret is `SHOPIFY_CLIENT_SECRET`.
