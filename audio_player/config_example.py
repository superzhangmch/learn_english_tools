"""Example config for the ✨ select-to-explain feature.

    cp config_example.py config.py     # then edit config.py (it is gitignored)

config.py is the SINGLE source of truth for models/keys. It holds real API keys and
must NEVER be committed (it is gitignored). If config.py is absent, the app falls back
to the LLM_BASE_URL / LLM_MODEL / LLM_API_KEY / LLM_THINKING environment variables
(single model). If neither is set, the interpret feature is off.

IMPORTANT: run.sh is git-tracked — do NOT put real API keys in it. Keys live only here
in config.py. (run.sh should set only LYRICS_ROOT / HOST / PORT.)

MODELS: the model menu shown in the ⚙ settings dropdown. First entry = default.
Each entry is an OpenAI-compatible endpoint (base_url + /chat/completions, streaming).
The browser only ever sees each model's display `name`; keys stay server-side.
Optional `extra` is merged into the request payload — put ANY per-model request field
here, e.g.:
  - GLM is a thinking model: {"thinking": {"type": "disabled"}} (else it returns only
    reasoning_content and the answer is empty);
  - temperature: {"temperature": 0.3} (omit it for models like Bedrock claude that
    reject a temperature field).
"""
MODELS = [
    {"name": "claude-sonnet-4.7", "base_url": "http://localhost:4000/v1",
     "api_key": "sk-your-local-proxy-key", "model": "claude-sonnet-4-7-1m"},
    # {"name": "glm-5.2", "base_url": "https://open.bigmodel.cn/api/paas/v4",
    #  "api_key": "your-zhipu-key", "model": "glm-5.2",
    #  "extra": {"thinking": {"type": "disabled"}}},
    # {"name": "deepseek", "base_url": "https://api.deepseek.com",
    #  "api_key": "sk-...", "model": "deepseek-chat"},
]
