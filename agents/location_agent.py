from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from llm import chat_json

_geocoder = Nominatim(user_agent="shopping-agent/1.0")


def geocode(location_text: str) -> dict | None:
    try:
        loc = _geocoder.geocode(location_text, timeout=10, addressdetails=True)
        if loc:
            return {
                "display": loc.address,
                "lat": loc.latitude,
                "lng": loc.longitude,
                "city": _extract_city(loc.raw),
            }
    except (GeocoderTimedOut, GeocoderServiceError):
        pass
    return None


def _extract_city(raw: dict) -> str:
    addr = raw.get("address", {})
    # Prefer suburb (Brooklyn) over city (New York) for neighborhood-level precision
    return (
        addr.get("suburb")
        or addr.get("neighbourhood")
        or addr.get("city")
        or addr.get("town")
        or addr.get("village")
        or addr.get("county")
        or raw.get("name")
        or ""
    )


async def parse_location_from_text(user_text: str) -> dict | None:
    """Extract and geocode location from freeform user text."""
    result = await chat_json([{
        "role": "user",
        "content": (
            f"Extract the location from this text. Return JSON: "
            f'{{\"location\": \"city, state or full address or null\"}}\n\nText: {user_text}'
        ),
    }])
    location_str = result.get("location")
    if not location_str:
        return None
    return geocode(location_str)


def reverse_geocode(lat: float, lng: float) -> dict | None:
    """Convert lat/lng to location dict."""
    try:
        loc = _geocoder.reverse(f"{lat},{lng}", timeout=10, addressdetails=True)
        if loc:
            return {
                "display": loc.address,
                "lat": lat,
                "lng": lng,
                "city": _extract_city(loc.raw),
            }
    except (GeocoderTimedOut, GeocoderServiceError):
        pass
    return None


def format_location_for_search(loc: dict) -> str:
    city = loc.get("city") or loc.get("display", "").split(",")[0]
    return city.strip()
