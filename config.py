import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Username for m3u/m3u8 playlists
USERNAME = os.getenv("USERNAME")
PASSWORD = os.getenv("PASSWORD")

# URL or file path
PLAYLIST_SOURCE = os.getenv("PLAYLIST_SOURCE", None)

FILTER_OUTPUT_FILENAME = "filtered_channels.m3u"
ORGANIZER_OUTPUT_FILENAME = "organized_channels.m3u"

RETRY_DELAY = 0.3
MAX_WORKERS = 800

# For m3ufilter.py
FILTER_KEYWORDS = []  # Example: ["disney +", "espn", "hbo"]
