"""Stateless public MLS lookup for temporary rental cash-flow modelling."""
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from lxml import html
from lxml.etree import ParserError
import server

PROVIDER = "https://www.calgarylistings.com"
MAX_PAGE_BYTES = 2_000_000


class ListingNotFound(RuntimeError):
    pass


class ListingUnavailable(RuntimeError):
    pass


def normalize_mls(value):
    mls = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]\d{7}", mls):
        raise ValueError("Enter an MLS number such as A2338340 (one letter and seven digits).")
    return mls


def _provider_url(url):
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in {"www.calgarylistings.com", "calgarylistings.com"} and parsed.port in {None, 443}


class ProviderRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _provider_url(newurl):
            raise ListingUnavailable("The listing provider redirected to an unsupported site.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    match = re.search(r"-?\d[\d,\s]*(?:\.\d+)?", str(value))
    if not match:
        return None
    result = float(re.sub(r"[,\s]", "", match.group()))
    return result if math.isfinite(result) and result >= 0 else None


def parse_listing(content, mls, source_url):
    mls = normalize_mls(mls)
    if not _provider_url(source_url):
        raise ListingUnavailable("Unsupported listing source.")
    try:
        page = html.fromstring(content)
    except (ParserError, ValueError) as exc:
        raise ListingUnavailable("The listing source returned an unreadable page.") from exc
    entities = []
    for script in page.xpath('//script[@type="application/ld+json"]'):
        try:
            data = json.loads(script.text or "")
        except (ValueError, TypeError):
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict):
                entities.extend(node.get("@graph", [node]))
    def types(node):
        value = node.get("@type", [])
        return value if isinstance(value, list) else [value]
    def matches_url(url):
        return _provider_url(url) and re.match(rf"^/calgary-mls/{mls}(?:-|/|$)", urllib.parse.urlsplit(url).path, re.I)
    listing = next((node for node in entities if isinstance(node, dict) and "RealEstateListing" in types(node)
                    and matches_url(node.get("url", ""))), None)
    if listing is None:
        raise ListingNotFound(f"MLS {mls} was not found on CalgaryListings.com. It may be unavailable or outside this source's coverage.")
    residence_id = (listing.get("mainEntity") or {}).get("@id")
    residence = next((node for node in entities if isinstance(node, dict) and residence_id and node.get("@id") == residence_id), {})
    identifier = str(residence.get("identifier", ""))
    if identifier and not re.search(rf"\b{mls}\b", identifier, re.I):
        raise ListingUnavailable("The returned listing did not match the requested MLS number.")
    def field(label):
        for element in page.xpath('//*[normalize-space(text())=$label]', label=label):
            parent = element.getparent()
            if parent is not None:
                others = [child for child in parent if child is not element]
                if others:
                    return " ".join(others[-1].itertext()).strip()
        return None
    offers = listing.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    currency = offers.get("priceCurrency", "CAD")
    if currency != "CAD":
        raise ListingUnavailable("The listing price is not in CAD.")
    address = residence.get("address") or listing.get("address") or {}
    address_text = ", ".join(str(address.get(key)) for key in ["streetAddress", "addressLocality", "addressRegion", "postalCode"] if address.get(key))
    price = _number(offers.get("price") if offers.get("price") is not None else field("List Price"))
    if price is None or price <= 0:
        raise ListingUnavailable("The listing provider did not return a valid asking price.")
    taxes_text = field("Property Taxes")
    tax_year = re.search(r"\((20\d{2})\)", taxes_text or "")
    images = residence.get("image") or listing.get("image") or []
    if not isinstance(images, list):
        images = [images]
    photo_url = None
    for image in images:
        url = image.get("url") or image.get("contentUrl") if isinstance(image, dict) else image
        if isinstance(url, str) and _provider_url(url):
            photo_url = url
            break
    return {"mls": mls, "address": address_text or residence.get("name") or listing.get("name", ""),
            "price": price, "condo_fee_monthly": _number(field("Monthly Condo Fee")),
            "property_tax_annual": _number(taxes_text), "tax_year": int(tax_year.group(1)) if tax_year else None,
            "bedrooms": _number(residence.get("numberOfBedrooms") if residence.get("numberOfBedrooms") is not None else field("Bedrooms")),
            "bathrooms": _number(residence.get("numberOfBathroomsTotal") if residence.get("numberOfBathroomsTotal") is not None else field("Total Bathrooms")),
            "source_url": listing["url"], "source_name": "CalgaryListings.com",
            "source_updated": listing.get("dateModified"), "photo_url": photo_url}


def fetch_listing(mls):
    mls = normalize_mls(mls)
    request = urllib.request.Request(f"{PROVIDER}/calgary-mls/{mls}/", headers={"User-Agent": "Mozilla/5.0 (compatible; PortfolioTracker/1.0)", "Accept": "text/html"})
    try:
        opener = urllib.request.build_opener(ProviderRedirect())
        with opener.open(request, timeout=20) as response:
            if not _provider_url(response.url):
                raise ListingUnavailable("Unsupported listing source.")
            content = response.read(MAX_PAGE_BYTES + 1)
            if len(content) > MAX_PAGE_BYTES:
                raise ListingUnavailable("The listing page is too large to process.")
            return parse_listing(content, mls, response.url)
    except urllib.error.HTTPError as exc:
        if exc.code in {404, 410}:
            raise ListingNotFound(f"MLS {mls} was not found on CalgaryListings.com.") from exc
        raise ListingUnavailable("The listing source is unavailable. Try again later.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ListingUnavailable("Could not reach the listing source. Try again later.") from exc


def clear_saved_listings():
    """Remove storage from the former saved-comparisons feature on app startup."""
    with closing(server.get_connection()) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS rental_condo_listings")
