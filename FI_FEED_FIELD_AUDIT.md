# Finland feed field audit

Audit date: 2026-09-12. Merchant Center account: `5052901478`.

## Live replacement status

The production cutover completed on 2026-09-12. The old Simprosys source
`10654636924` was removed and replaced by the scheduled URL source
`Vuodevaatteet FI – Own Production`, ID `10728562355`.

- Country/language/feed label: Finland / Finnish / `FI`
- Marketing methods: Free listings and Shopping ads
- Scheduled fetch: every 24 hours at 06:00 Europe/Helsinki, after the GitHub build
- First fetch: 126,725 offers updated, six new, all attributes recognized
- Source-file errors: none
- Stable source-table count after processing: 126,720
- Verified Royal Textile offer: approved, visible on Google and in ads; Finnish
  landing page, price/sale price, GTIN/MPN, availability, variant size, and all
  five variant labels were correct

The isolated `FI-PILOT` source remains paused and separate during the observation
period. Simprosys automatically recreated a Merchant API source with five items
after the initial deletion. Its Google OAuth access was therefore revoked and
the recreated source `10728797040` was deleted. A follow-up check showed only the
own production source and isolated pilot. The Simprosys Shopify subscription has
not been cancelled.

## Current Simprosys source

- Source: `Simprosys Feed (Merchant API)`, ID `10654636924`
- Country/language/feed label: Finland / Finnish / `FI`
- Marketing methods: free listings and Shopping ads
- Attribute rules: default rule only; no custom GMC rules
- Scope: all active and published Shopify products, all variants
- Simprosys count: 125,517 product cards; GMC count during audit: 126,720 offers
- Default title and rich HTML description; variant names appended to titles
- Main image plus additional images
- Continue-selling variants submitted in stock
- Identifier policy: brand + MPN/SKU + valid GTIN

## Attributes observed in GMC and Simprosys

The current source supplies the following product/offer data when available:

`id`, `title`, `description`, `link`, `canonical_link`, `image_link`,
`additional_image_link`, `availability`, `price`, `sale_price`, `brand`, `mpn`,
`gtin`, `google_product_category`, `product_type`, `item_group_id`,
`shipping_weight`, `cost_of_goods_sold`, `color`, `size`, `material`, `pattern`,
`condition`, `age_group`, `gender`, `size_system`, `size_type`, `adult`,
`unit_pricing_measure`, `unit_pricing_base_measure`, `custom_label_0` through
`custom_label_4`, `shipping_label`, and `return_policy_label`.

GMC also displays generated Shopify metadata such as `merchant_item_id`, Shopify
product GID, store domain, item group title, and variant options. These are not
required RSS feed attributes and are reconstructed from standard feed fields.

## Label examples

| Supplier/sample | label 0 | label 1 | label 2 | current GMC label 3 | current GMC label 4 |
|---|---|---|---|---|---|
| NovaEngel perfume | `NovaEngel` | `G0046` | `NOADS` | `NOVA-PROFIT` | `NOVA-TEST` |
| Royal Textile sheet | `New Royal Textile` | `G0039` | `NOADS` | `ROYAL-CORE` | `ROYAL-TEST` |
| VidaXL wall clock | `vidaxl` | `G0004` | `NOADS` | `VIDAXL-LOW` | `VIDAXL-TEST` |

Labels 0–4 are offer-level data: the replacement feed reads them only from each
Shopify variant's `mm-google-shopping` metafields. It does not inherit labels
from the parent product. Labels 3 and 4 visible in Simprosys product editing can
differ from the values actually sent to GMC. GMC has no rules that cause this
difference, so it is internal to Simprosys. The replacement intentionally uses
the current variant values in Shopify as the source of truth, as approved for
the migration. The pilot and full report preserve both field coverage and every
variant label value sourced directly from Shopify.

## Pilot safety

The pilot uses distinct `pilot_FI_...` offer and group IDs, excludes
`Shopping_ads` and `Display_ads`, and submits `pause=all`. The source itself is
limited to free listings. This leaves one valid diagnostic destination while
preventing the pilot offers from being shown. The feed uses the approved 1 kg
fallback only when Shopify has no positive variant weight.

Shopify taxonomy or the `mm-google-shopping.google_product_category` metafield
is used first. NovaEngel perfume products without either value receive the
official Google category `479` (Perfume & Cologne), avoiding an avoidable
category gap without guessing categories for unrelated product types.

## Complete production candidate

The first complete candidate was generated and validated on 2026-09-12. It is
not connected to Merchant Center.

| Metric | Own candidate | Current Simprosys/GMC comparison |
|---|---:|---:|
| Active and published Shopify products read | 125,516 | Simprosys UI audit: 125,517 |
| Products emitted | 124,844 | Products without a usable offer are excluded |
| Variant offers | 126,725 | GMC audit: 126,720 |
| Unique, parsed offer IDs | 126,725 | Difference from GMC: +5 (0.004%) |
| Offers with valid GTIN | 125,119 | Invalid non-empty GTIN values: 1,349 |
| Offers with MPN/SKU | 126,436 | — |
| Offers with COGS | 126,723 | Missing: 2 |
| Offers with Google category | 87,592 | Missing: 39,133 |
| Offers with sale price | 69,961 | — |
| 1 kg weight fallbacks | 10,629 | Explicitly approved fallback |
| Description fallbacks | 6 | Finnish deterministic template |

The candidate excluded 676 product/variant rows without a required title,
handle, or image and four variants without a positive price. This is consistent
with the near-identical final offer count: the current GMC source also cannot
publish unusable offers. The one-product difference in the Shopify scope and
five-offer difference are within normal catalog movement during two audits, but
must be checked again immediately before cutover.

All 126,725 offers have `custom_label_0`. Missing variant labels are:

- `custom_label_1`: 18,876
- `custom_label_2`: 664
- `custom_label_3`: 16,912
- `custom_label_4`: 16,900

These are real empty variant metafields and are not filled from the parent
product. The generated report preserves every observed value and its count.
The production candidate has no `pause` or destination exclusions, but remains
safe because its URL has not been connected to Merchant Center.
