# Finland feed field audit

Audit date: 2026-09-12. Merchant Center account: `5052901478`.

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

The Shopify `mm-google-shopping` metafields contain labels 0–4, but labels 3 and
4 visible in Simprosys product editing can differ from the values actually sent
to GMC. GMC has no rules that cause this difference, so it is internal to
Simprosys. The pilot reports both field coverage and every label value sourced
directly from Shopify. Before a full cutover, the production label 3/4 logic
must be recreated explicitly or exported from Simprosys.

## Pilot safety

The pilot uses distinct `pilot_FI_...` offer and group IDs and submits repeated
`excluded_destination` values for `Shopping_ads`, `Display_ads`, and
`Free_listings`. It can therefore be inspected in GMC without replacing or
advertising the live Simprosys offers. The feed uses the approved 1 kg fallback
only when Shopify has no positive variant weight.

Shopify taxonomy or the `mm-google-shopping.google_product_category` metafield
is used first. NovaEngel perfume products without either value receive the
official Google category `479` (Perfume & Cologne), avoiding an avoidable
category gap without guessing categories for unrelated product types.
