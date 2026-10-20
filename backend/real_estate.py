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


def fee_inclusion_status(items):
    """Use fee inclusions only, never the property's heating system or amenities."""
    patterns = {"electricity": r"\b(?:electricity|electric|electrical|hydro|power)\b",
                "heat": r"\b(?:heat|heating)\b", "parking": r"\bparking\b"}
    evidence = {key: set() for key in patterns}
    for item in items:
        text = item.lower()
        if re.search(r"\bnot (?:specified|listed|confirmed|known)\b", text):
            continue
        excluded = bool(re.search(r"\b(?:not included|not covered|excluded|extra charge|paid separately|separately metered|tenant.paid|owner.paid)\b|^\s*(?:no|not)\s", text))
        for key, pattern in patterns.items():
            if not re.search(pattern, text):
                continue
            if key == "parking" and re.search(r"\b(?:visitor|guest)\b", text):
                continue
            if re.search(r"\b(?:common areas?|shared areas?)\b", text) or (key == "parking" and re.search(r"\bmaintenance\b", text)):
                continue
            evidence[key].add(not excluded)
    return {key: next(iter(values)) if len(values) == 1 else None for key, values in evidence.items()}


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
    size = residence.get("floorSize") or {}
    square_feet = None
    if isinstance(size, dict):
        area = _number(size.get("value"))
        unit = str(size.get("unitCode") or size.get("unitText") or "").lower()
        if area is not None and area > 0:
            if unit in {"ftk", "ft2", "ft²", "sqft", "sq ft"} or "square feet" in unit or "square foot" in unit:
                square_feet = area
            elif unit in {"mtk", "m2", "m²", "sqm", "sq m"} or "square met" in unit:
                square_feet = area / 0.09290304
    if square_feet is None:
        for label in ["Above-Grade Sq Ft", "Living Area Sq Ft", "Square Footage", "Sq ft"]:
            text = field(label)
            square_feet = _number(text)
            if square_feet is not None and re.search(r"\bm2\b|m²|\bsq\.?\s*m\b|square met", text or "", re.I):
                square_feet /= 0.09290304
            if square_feet is not None:
                break
    if square_feet is not None and square_feet <= 0:
        square_feet = None
    fee_text = next((value for label in ["Fee Includes", "Association Fee Includes", "Condo Fee Includes"] if (value := field(label))), "")
    fee_includes = [part.strip() for part in re.split(r"[·,;\n]+", fee_text) if part.strip() and part.strip().lower() not in {"n/a", "none", "not available", "not listed"}]
    condo_fee = _number(field("Monthly Condo Fee"))
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
            "price": price, "condo_fee_monthly": condo_fee,
            "square_feet": square_feet,
            "condo_fee_per_sqft_monthly": condo_fee / square_feet if condo_fee is not None and square_feet else None,
            "condo_fee_includes": fee_includes, "condo_fee_inclusions": fee_inclusion_status(fee_includes),
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
