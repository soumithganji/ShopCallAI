"""
Store search using Google Maps Places API (Nearby Search).
Phone numbers come directly from nearby search results — no extra Place Details calls needed.
"""
import asyncio
import re
import httpx
import config


async def find_stores(
    product: str,
    location: str,
    product_context: str = "",
    lat: float | None = None,
    lng: float | None = None,
) -> list[dict]:
    if lat is None or lng is None:
        coords = await _geocode(location)
        if not coords:
            return []
        lat, lng = coords

    keyword = f"{product_context} {product}".strip() if product_context else product
    places = await _nearby_search(lat, lng, keyword)

    if not places:
        # Fallback to generic store type
        places = await _nearby_search(lat, lng, _guess_store_type(product))

    stores: list[dict] = []
    for p in places[:5]:
        phone_raw = p.get("international_phone_number") or p.get("formatted_phone_number", "")
        if not phone_raw:
            # Fetch details only if phone missing from nearby result
            details = await _get_place_phone(p["place_id"])
            phone_raw = details or ""

        e164 = _to_e164(phone_raw)
        if not e164:
            continue

        stores.append({
            "name": p.get("name", ""),
            "address": p.get("vicinity", ""),
            "phone": e164,
            "rating": p.get("rating"),
            "open_now": p.get("opening_hours", {}).get("open_now"),
            "place_id": p["place_id"],
            "confidence": 1.0,
        })

    return stores


async def _geocode(location: str) -> tuple[float, float] | None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            config.GMAPS_GEOCODE_URL,
            params={"address": location, "key": config.GOOGLE_MAPS_API_KEY},
        )
        results = resp.json().get("results", [])
        if results:
            loc = results[0]["geometry"]["location"]
            return loc["lat"], loc["lng"]
    return None


async def _nearby_search(lat: float, lng: float, keyword: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            config.GMAPS_NEARBY_URL,
            params={
                "location": f"{lat},{lng}",
                "radius": config.GMAPS_SEARCH_RADIUS_METERS,
                "keyword": keyword,
                "rankby": "prominence",  # prominence within tight radius = relevant + close
                "key": config.GOOGLE_MAPS_API_KEY,
            },
        )
        return resp.json().get("results", [])


async def _get_place_phone(place_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            config.GMAPS_DETAILS_URL,
            params={
                "place_id": place_id,
                "fields": "international_phone_number",
                "key": config.GOOGLE_MAPS_API_KEY,
            },
        )
        result = resp.json().get("result", {})
        return result.get("international_phone_number")


def _to_e164(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


def _guess_store_type(product: str) -> str:
    p = product.lower()
    if any(w in p for w in ("tape", "tool", "nail", "screw", "drill", "paint", "pipe", "lumber")):
        return "hardware store"
    if any(w in p for w in ("book", "notebook", "pen", "pencil", "paper", "binder", "staple")):
        return "office supply store"
    if any(w in p for w in ("vitamin", "medicine", "pill", "bandage", "first aid", "aspirin")):
        return "pharmacy"
    if any(w in p for w in ("shirt", "pants", "shoes", "jacket", "dress", "sock")):
        return "clothing store"
    if any(w in p for w in ("phone", "cable", "charger", "headphone", "laptop", "battery")):
        return "electronics store"
    return "store"
