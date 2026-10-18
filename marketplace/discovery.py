"""Truthful, host-aware public discovery markup for KOFAD's indexable pages.

No fabricated reviews, corporate registration, worldwide shipping or locations.
The site name and product data come from actual public pages and listings.
"""
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings

ORG = "https://kofadimpex.com/#organization"
PAGE_NAMES = {
    "/": "KOFAD IMPEX ENTERPRISE",
    "/apps/": "Download KOFAD Market app",
    "/about/": "About KOFAD",
    "/wholesale/": "Wholesale & bulk supply",
    "/faq/": "Help and frequently asked questions",
    "/delivery/": "Delivery and collection",
    "/returns-policy/": "Returns and refunds",
    "/contact/": "Contact KOFAD",
    "/terms/": "Terms of service",
    "/privacy/": "Privacy notice",
    "/market/": "KOFAD Market",
}


def page_title(path, title, category_name=None, listing=None):
    if path == "/":
        return "KOFAD IMPEX ENTERPRISE | Retail & Wholesale Shopping in Ghana"
    if path == "/market/":
        return "KOFAD Market | Shop Retail & Wholesale Products in Ghana"
    if path == "/wholesale/":
        return "Wholesale & Bulk Supply in Ghana | KOFAD"
    public_titles = {
        "/apps/": "Download KOFAD Market App | Shopping in Ghana",
        "/about/": "About KOFAD IMPEX ENTERPRISE | Ghana",
        "/faq/": "Help & FAQs | KOFAD IMPEX ENTERPRISE",
        "/delivery/": "Delivery & Collection | KOFAD Ghana",
        "/returns-policy/": "Returns & Refunds | KOFAD Ghana",
        "/contact/": "Contact KOFAD IMPEX ENTERPRISE | Customer Care",
        "/terms/": "Terms of Service | KOFAD IMPEX ENTERPRISE",
        "/privacy/": "Privacy Notice | KOFAD IMPEX ENTERPRISE",
    }
    if path in public_titles:
        return public_titles[path]
    if category_name:
        return f"{category_name} Products in Ghana | KOFAD Market"
    if listing:
        return f"{listing.display_name} | KOFAD Market Ghana"
    return f"{title or PAGE_NAMES.get(path, 'KOFAD')} | KOFAD IMPEX ENTERPRISE"


def description(path, page=None, listing=None, category_name=None):
    if listing:
        return ((listing.description or "") or
                f"{listing.display_name} at KOFAD Market. See current pricing, "
                "selling unit and availability before ordering in Ghana.")[:180]
    if category_name:
        return (f"Browse {category_name} products listed at KOFAD Market in Ghana. "
                "Check current prices, pack sizes and availability before ordering.")[:180]
    if path == "/":
        return ("KOFAD IMPEX ENTERPRISE serves retail and wholesale buyers in Ghana. "
                "Browse KOFAD Market for current products, pack sizes and prices; "
                "ask about bulk supply, delivery or collection.")
    if path == "/market/":
        return ("Shop available retail and wholesale products at KOFAD Market in Ghana. "
                "Explore current categories, prices in Ghana cedis, pack sizes and "
                "delivery or collection options.")
    if path == "/wholesale/":
        return ("Ask KOFAD IMPEX ENTERPRISE about wholesale quantities, business "
                "restocking and bulk orders in Ghana. Request availability, pack sizes "
                "and a quotation before placing a special order.")
    public_descriptions = {
        "/apps/": "Get the KOFAD Market customer shopping app. Check available official Android, iOS and desktop releases and installation information.",
        "/about/": "Learn about KOFAD IMPEX ENTERPRISE, our Ghana retail and wholesale business, product catalogue, customer service and order fulfilment.",
        "/faq/": "Find KOFAD answers about shopping, payment verification, delivery, collection, returns and account support before or after you order.",
        "/delivery/": "Learn about KOFAD delivery coverage, pickup, fees, tracking and customer handover. Check available options and charges at checkout.",
        "/returns-policy/": "Read KOFAD's returns, cancellations and refunds policy, including eligibility, how to request support and refund processing.",
        "/contact/": "Contact KOFAD IMPEX ENTERPRISE in Ghana for product enquiries, wholesale quotations, order support or customer feedback.",
        "/terms/": "Read the terms for shopping with KOFAD Market, including orders, payment confirmation, fulfilment and customer responsibilities.",
        "/privacy/": "Learn how KOFAD IMPEX ENTERPRISE handles customer account information, order data, communications and privacy choices.",
    }
    if path in public_descriptions:
        return public_descriptions[path]
    return ((page or {}).get("intro") or
            "Explore retail and wholesale shopping, delivery and customer support "
            "from KOFAD IMPEX ENTERPRISE in Ghana.")[:180]


def _breadcrumb(url, path, name, category_name=None):
    if path == "/":
        return None
    items = [
        {"@type": "ListItem", "position": 1,
         "name": "Home", "item": settings.PUBLIC_SITE_ORIGIN + "/"},
    ]
    if path.startswith("/market/") and path != "/market/":
        items.append({
            "@type": "ListItem", "position": 2, "name": "KOFAD Market",
            "item": settings.MARKET_SITE_ORIGIN + "/market/",
        })
    items.append({"@type": "ListItem", "position": len(items) + 1,
                  "name": category_name or name, "item": url})
    return {"@type": "BreadcrumbList", "itemListElement": items}


def structured_data(path, url, title, summary, listing=None, category_name=None):
    """Return safely JSON-encoded schema markup only for an indexable public page."""
    company = settings.PUBLIC_SITE_ORIGIN
    market = settings.MARKET_SITE_ORIGIN
    market_page = path.startswith("/market/")
    # The market subdomain's actual public landing page is /market/, not /.
    # Keep WebSite.url, @id and WebPage.isPartOf on the same canonical home.
    website_home = market + "/market/" if market_page else company + "/"
    website_id = website_home + "#website"
    brand = {
        "@type": "Organization", "@id": ORG, "name": "KOFAD IMPEX ENTERPRISE",
        "alternateName": "KOFAD IMPEX", "url": company + "/",
        "logo": company + "/static/brand/kofad-logo-transparent.png",
        "description": "Retail and wholesale shopping and customer support in Ghana.",
        "areaServed": {"@type": "Country", "name": "Ghana"},
    }
    graph = []
    if path == "/":
        graph.append(brand)
    if path in {"/", "/market/"}:
        graph.append({
            "@type": "WebSite",
            "@id": website_id,
            "name": "KOFAD Market" if market_page else "KOFAD IMPEX ENTERPRISE",
            "alternateName": "KOFAD" if not market_page else "KOFAD IMPEX Market",
            "url": website_home,
            "publisher": {"@id": ORG}, "inLanguage": "en-GH",
        })
    page_type = "CollectionPage" if market_page and not listing else "WebPage"
    page_node = {
        "@type": page_type, "@id": url + "#webpage", "url": url,
        "name": title, "description": summary, "inLanguage": "en-GH",
        "isPartOf": {"@id": website_id},
    }
    graph.append(page_node)
    crumb = _breadcrumb(url, path, PAGE_NAMES.get(path, title), category_name)
    if crumb:
        graph.append(crumb)
    if listing:
        node = {
            "@type": "Product", "@id": url + "#product",
            "name": str(listing.display_name),
            "description": str(listing.description or
                               "See current pricing, unit and availability at KOFAD Market."),
            "category": str(listing.product.category or "Products"),
            "sku": str(listing.product.sku),
        }
        if listing.image_data:
            node["image"] = market + f"/market/products/{listing.pk}/image/large/"
        try:
            price = Decimal(str(listing.market_price_value))
        except (InvalidOperation, AttributeError, TypeError):
            price = Decimal("-1")
        if price >= 0 and price.is_finite():
            node["offers"] = {
                "@type": "Offer", "url": url, "priceCurrency": "GHS",
                "price": format(price, ".2f"),
                "availability": ("https://schema.org/InStock" if
                                 listing.available_sell_qty else
                                 "https://schema.org/OutOfStock"),
                "seller": {"@id": ORG},
            }
        graph.append(node)
    # No user-provided strings can terminate the JSON-LD script element.
    return json.dumps({"@context": "https://schema.org", "@graph": graph},
                      ensure_ascii=True, separators=(",", ":")).replace(
                          "<", "\\u003c").replace(">", "\\u003e").replace(
                              "&", "\\u0026")
