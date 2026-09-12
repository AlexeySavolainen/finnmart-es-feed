# Finnmart Spain Merchant feed

Validated 100-product pilot feed for the Finnmart Spain market.

- 50 NovaEngel products
- 50 Royal Textile products
- Spanish Shopify landing pages only
- Stable `shopify_ES_` offer IDs
- Spain shipping submitted per offer: `Entrega estándar (3–5 días laborables)`
- Live Shopify weight determines the matching `11.50 EUR` or `13.10 EUR` rate
- No dependency on the Finland feed or Simprosys

Public feed endpoint after deployment:

`/finnmart-es-pilot.xml`

## Full Market synchronization

`sync_feed.py` reads only Shopify MarketCatalog
`gid://shopify/MarketCatalog/166288720196`, requires the exact catalog title,
allows only NovaEngel and Royal Textile tags, requires reviewed Spanish
translations, and validates every Spanish storefront response before publishing.

The scheduled GitHub Actions workflow builds the full feed daily and deploys the
`public` directory to GitHub Pages without committing generated XML files to Git
history. The required repository secret is `SHOPIFY_CLIENT_SECRET`.

The pilot endpoint stays unchanged until the first full run passes validation.
