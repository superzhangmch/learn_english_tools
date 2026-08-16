# Copy to config.py (gitignored) and fill in. Real endpoints and keys live only
# in config.py; the browser never sees them, since /api/models exposes the
# display "name" alone.

BOOK_PATH = "/path/to/book.epub"

HOST = "127.0.0.1"          # bind wider only if you mean to expose the reader
PORT = 8400

# Any OpenAI-compatible chat endpoint. The first entry is the default, and the
# menu order is what the reader sees, so put the one you want reaching for first.
#
# Call each entry end-to-end before listing it: a proxy will advertise models
# whose deployments fail only on use, and a menu item that breaks when you pick it
# mid-sentence is worse than no item.
MODELS = [
    {
        "name": "<display name>",              # shown in the ⚙ menu
        "base_url": "https://<your-endpoint>/v1",
        "api_key": "<your-api-key>",
        "model": "<model-id>",

        # Optional, both for quirks that only show up on newer models:
        #
        # "temperature": None,
        #     omits the parameter entirely. Some models now reject it outright
        #     ("`temperature` is deprecated for this model"). Note that `extra`
        #     cannot do this — overriding replaces a value, it cannot remove a key.
        #
        # "extra": {"reasoning_effort": "none"},
        #     merged into the request body verbatim. Reasoning models bill hidden
        #     thinking against max_tokens while only delta.content is forwarded,
        #     so a long think can leave nothing for the answer; turning it down
        #     avoids that and is faster. The spelling differs by vendor.
    },
]

# Fallbacks, used only if MODELS is empty.
LLM_BASE_URL = MODELS[0]["base_url"]
LLM_MODEL = MODELS[0]["model"]
LLM_API_KEY = MODELS[0]["api_key"]
