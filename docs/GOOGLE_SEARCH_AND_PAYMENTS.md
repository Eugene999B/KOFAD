# KOFAD Google Search visibility

KOFAD uses three canonical HTTPS origins:

- `https://kofadimpex.com/`: the public company pages.
- `https://market.kofadimpex.com/market/`: the public market and published products.
- `https://staff.kofadimpex.com/`: the private staff workspace; keep it out of search engines.

## Public discovery

After the release has passed verification and is deployed:

1. Check `https://kofadimpex.com/robots.txt` and `https://kofadimpex.com/sitemap.xml` return HTTP 200.
2. Check `https://market.kofadimpex.com/robots.txt` and `https://market.kofadimpex.com/sitemap.xml` return HTTP 200. Only enabled, active listings belong in the market sitemap.
3. Check `https://staff.kofadimpex.com/robots.txt` says `Disallow: /`, `/sitemap.xml` is not published and authenticated workspace responses carry `X-Robots-Tag: noindex, nofollow, noarchive`.
4. In [Google Search Console](https://search.google.com/search-console), add **Domain property** `kofadimpex.com`, then verify it via the DNS TXT record provided by Google. A Domain property includes the company, Market and staff subdomains.
5. Submit both complete sitemap URLs above in Search Console. Optionally add the `https://market.kofadimpex.com/` URL-prefix property to monitor Market separately.
6. Use **URL Inspection** to request indexing of the main homepage, public company information, market page and priority products. Review the Pages/Indexing report over time.

Sitemaps and Search Console are discovery aids, not guarantees of Google ranking or indexing. Private accounts, cart pages, checkout, payment callbacks, staff tools and receipts must never appear in public sitemaps.

## Payment deployment acceptance

The **same company gateway setting** in `/settings/online-payments/` determines newly initiated cashier and customer transactions. An already initiated reference remains with its original provider. Paystack cashier MoMo uses provider-issued OTP when a charge explicitly enters `send_otp`; never ask for, log or submit a customer's MoMo wallet PIN. Hubtel cashier MoMo opens the verified secure `pay.hubtel.com` checkout instead. Do not post any sale until the provider status, amount, currency, reference, transaction identity and channel are independently verified by KOFAD.

Before collecting real customer money, run controlled low-value live payments through each enabled provider (on each supported network), a denied/expired payment, tab-close/background reconciliation, duplicate checkout protection and a payment followed by receipt and stock reconciliation. The mock-provider CI tests cannot certify live merchant credentials or network approval behaviour.
