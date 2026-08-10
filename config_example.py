"""Example config. Copy this to config.py (gitignored) and fill in your values:

    cp config_example.py config.py   # then edit config.py

Environment variables of the same names (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY /
HOST / PORT) override config.py.
"""
# Weather location — your latitude / longitude and a display name.
CITY = "Beijing"
LAT, LON = 39.9042, 116.4074

# LLM: an OpenAI-compatible endpoint (e.g. a local LiteLLM proxy).
LLM_BASE_URL = "http://localhost:4000/v1"
LLM_MODEL = "your-model-id"
LLM_API_KEY = "sk-your-key"

# Web server bind
HOST = "0.0.0.0"
PORT = 8000
