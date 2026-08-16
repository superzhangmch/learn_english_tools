# learn_english

Three small self-hosted tools for reading English, sharing one
"select text → LLM interpretation" interaction.

| | |
|---|---|
| `news_reader/` | news reader: clock dashboard, phone list, reading mode |
| `book_reader/` | epub reader |
| `audio_player/` | audio player + transcript |

Each subdirectory is self-contained and runs on its own. Copy its
`config_example.py` to `config.py` (gitignored — API keys live there and are
never committed) and follow its own README.
