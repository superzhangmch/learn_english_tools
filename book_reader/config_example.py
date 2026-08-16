# Copy to config.py (gitignored) and adjust. API keys stay in config.py and are
# never sent to the browser — the UI only ever sees the display "name".

BOOK_PATH = "/path/to/book.epub"

HOST = "0.0.0.0"
PORT = 8400

# Interpret model menu — the first entry is the default.
MODELS = [
    {"name": "claude-sonnet-4.6", "base_url": "http://localhost:4000/v1",
     "api_key": "sk-local", "model": "claude-sonnet-4-6"},
    {"name": "claude-haiku-4.5", "base_url": "http://localhost:4000/v1",
     "api_key": "sk-local", "model": "claude-haiku-4-5"},
]

LLM_BASE_URL = MODELS[0]["base_url"]
LLM_MODEL = MODELS[0]["model"]
LLM_API_KEY = MODELS[0]["api_key"]
