# learn_english_tools

Four small self-hosted tools for reading English.

| | |
|---|---|
| `news_reader/` | news reader: clock dashboard, phone list, reading mode |
| `book_reader/` | epub reader |
| `audio_player/` | audio player + transcript |
| `etymology_dict/` | custom macOS Dictionary.app dictionary of Chinese etymologies |

The first three are web apps sharing one "select text → LLM interpretation"
interaction; the fourth builds a dictionary the system's own lookup can use.

Each subdirectory is self-contained and runs on its own. Where there is a
`config_example.py`, copy it to `config.py` (gitignored — API keys live there and
are never committed) and follow that subdirectory's own README.
