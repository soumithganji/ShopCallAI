import os
from dotenv import load_dotenv

load_dotenv()

BASETEN_API_KEY = os.environ["BASETEN_API_KEY"]
BASETEN_BASE_URL = os.getenv("BASETEN_BASE_URL", "https://inference.baseten.co/v1")
BASETEN_MODEL = os.getenv("BASETEN_MODEL", "")

YOU_API_KEY = os.environ["YOU_API_KEY"]
YOU_SEARCH_URL = "https://ydc-index.io/v1/search"

GOOGLE_MAPS_API_KEY = os.environ["GOOGLE_MAPS_API_KEY"]
GMAPS_NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
GMAPS_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
GMAPS_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GMAPS_SEARCH_RADIUS_METERS = 2000  # ~1.2 miles — tight for dense urban areas

VOICERUN_API_KEY = os.environ["VOICERUN_API_KEY"]
VOICERUN_AGENT_ID = os.environ["VOICERUN_AGENT_ID"]
VOICERUN_FROM_PHONE = os.environ["VOICERUN_FROM_PHONE"]
VOICERUN_WEBHOOK_URL = os.environ["VOICERUN_WEBHOOK_URL"]
VOICERUN_API_BASE = "https://api.voicerun.com/v1"

MONGODB_URI = os.environ["MONGODB_URI"]
MONGODB_DB = os.environ["MONGODB_DB"]

CALL_MAX_RETRIES = 2
CALL_RETRY_DELAY_SECONDS = 20
CALL_POLL_TIMEOUT_SECONDS = 90
CALL_POLL_INTERVAL_SECONDS = 3
