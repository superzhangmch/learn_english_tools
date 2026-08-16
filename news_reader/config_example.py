"""Example config. Copy this to config.py (gitignored) and fill in your values:

    cp config_example.py config.py   # then edit config.py

Environment variables of the same names (HOST / PORT / LLM_BASE_URL / LLM_MODEL /
LLM_API_KEY) override config.py. API keys must NOT be committed — they live only
in your private config.py.
"""
# Weather location — your latitude / longitude and a display name.
CITY = "Beijing"
LAT, LON = 39.9042, 116.4074

# Web server bind
HOST = "0.0.0.0"
PORT = 8000

# Interpret model menu — first entry is the default. Each is an OpenAI-compatible
# endpoint (base_url + /chat/completions). Keys stay server-side (never committed).
MODELS = [
    {"name": "local", "base_url": "http://localhost:4000/v1",
     "api_key": "sk-your-key", "model": "your-model-id"},
    # {"name": "deepseek-v4-flash", "base_url": "https://api.deepseek.com",
    #  "api_key": "sk-...", "model": "deepseek-v4-flash"},
]

LLM_BASE_URL = MODELS[0]["base_url"]
LLM_MODEL = MODELS[0]["model"]
LLM_API_KEY = MODELS[0]["api_key"]
