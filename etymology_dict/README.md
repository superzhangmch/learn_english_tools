# custom_mac_etymology_dict

A custom macOS Dictionary.app dictionary providing **Chinese etymology** for ~13k English words. Inflected and derived forms (`dogs` → dog, `claims` → claim, `legally` → legal, `children` → child, `went` → go, `teacher` → teach, ...) all resolve to their base entry via baked-in `<d:index>` aliases.

how to use: 

<img width="497" height="526" alt="image" src="https://github.com/user-attachments/assets/d2ddaa04-21e1-4378-b055-a4b7e531ffc8" />

> 中文版见 [README_zh.md](README_zh.md)

(By AI, 个人自娱自乐使用的)

## Requirements

- macOS (tested on Apple Silicon, should work on Intel too)
- Python 3.11+
- Xcode Command Line Tools: `xcode-select --install`
- Python deps: `pip3.11 install lemminflect nltk pyobjc-framework-CoreServices`

## Quick start

```bash
git clone https://github.com/superzhangmch/custom_mac_etymology_dict.git
cd custom_mac_etymology_dict

# Third-party build tools (Apple's official DDK is not freely redistributable,
# but this open-source clone provides the same binaries)
git clone https://github.com/jjgod/mac-dictionary-kit.git

./build.sh
```

Open **Dictionary.app → Preferences (⌘,) → enable EtymologyDict**.

Test it: select any word in any app and force-touch / press ⌃⌘D — should show the Chinese etymology.

## What's in `data/`

| File | Format | Source |
|---|---|---|
| `etymology_dict.json` | `{"word": "中文词源说明", ...}` (12.9k entries) | AI-generated etymology |
| `word_source.js` | `var word_source = {"word": [ref_cnt, ...], ...};` (26.6k entries; first element is per-word reference count across English word lists) | [zyronon/typing-word](https://github.com/zyronon/typing-word) |

## Build pipeline

`./build.sh` runs three steps:

1. **`build_full.py`** — reads data files, emits AppleDict XML with all alias indices into `objects/apple_src/EtymologyDict.xml`
2. **`make`** — calls `mac-dictionary-kit/ddk/build_dict.sh` which compiles the XML into a `.dictionary` bundle with binary key index
3. **`make install`** — copies to `~/Library/Dictionaries/`, then kills `LookupViewService` and `Dictionary` so caches refresh

Override paths via env vars:

```bash
DATA_DIR=~/my_data \
OUTPUT_DIR=./build \
DDK_PATH=./mac-dictionary-kit/ddk \
PYTHON=python3.12 \
./build.sh
```

## How aliases are generated

Apple's built-in dictionaries hard-bake every inflected form as a lookup key (you can verify by extracting `Body.data` from NOAD — `dogs`, `running`, etc. all appear as `<d:index>` entries on the base word's entry). **The system "Look Up" popup does NOT lemmatize for custom dictionaries.** If you only put `dog`, looking up `dogs` will fail.

So `build_full.py` forward-generates three layers of aliases for each base word:

1. **Inflection** via `lemminflect`:
   - `claim` → `claims, claimed, claiming`
   - `run` → `runs, running, ran`
   - `child` → `children`
   - `go` → `goes, going, gone, went`

2. **Adjective → adverb** via orthography rules:
   - `legal` → `legally`
   - `simple` → `simply` (drop `e`, add `y`)
   - `happy` → `happily` (`y` → `ily`)
   - `basic` → `basically` (`-ic` → `-ally`)
   - `true` → `truly` (drop `e`)

3. **Derivational forms** via WordNet:
   - `teach` → `teacher, teaching, teachable`
   - `create` → `creation, creator, creative, creature`

Final stats: 12.8k base entries → ~75k searchable keys.

## Important notes

### 1. Don't iterate `synset.lemmas()` blindly

When using WordNet for derivational forms, **only consider the lemma whose name matches the query word**:

```python
for syn in wn.synsets(key):
    for lemma in syn.lemmas():
        if lemma.name().lower() != key:
            continue                       # <-- critical
        for related in lemma.derivationally_related_forms():
            ...
```

WordNet groups synonyms into the same synset. E.g. `work` and `ferment` share `ferment.v.03`. Without the filter, `work` would inherit `ferment`'s derivations (`fermenting`, `fermentation`) — leading to wrong lookups like `fermenting` → "work" entry. This filter alone drops ~80% of false-positive aliases.

### 2. Cache refresh

Dictionary.app + `LookupViewService` cache aggressively. After reinstalling:

```bash
killall -9 LookupViewService Dictionary
```

If that's not enough, log out and back in, or reboot. `build.sh` does the kill automatically.

### 3. Inline `<span>`, not block `<h1>`/`<p>`

The Look Up popup gives each dictionary a fixed height. Block-level elements each start on a new line and waste vertical space — your entry will show only one line with a "更多" link. Use inline `<span>` for everything (see how Apple's built-in dicts do it), so content flows as a wrapping paragraph and you get 3–4 lines visible.

### 4. Inflected forms aren't system-lemmatized

For Apple's built-in dictionaries, `dogs` works because NOAD's `dog` entry includes `<d:index d:value="dogs"/>`. macOS doesn't lemmatize on your behalf — it just looks up the exact string. We replicate that mechanism by pre-generating aliases.

## Uninstall

```bash
rm -rf ~/Library/Dictionaries/EtymologyDict.dictionary
killall LookupViewService Dictionary
```

## Files

```
.
├── build.sh             # one-shot build + install + refresh
├── build_full.py        # generates AppleDict XML with aliases
├── data/                # bundled etymology + word_source data
├── README.md            # this file
└── README_zh.md         # Chinese version
```

## Acknowledgements

- [jjgod/mac-dictionary-kit](https://github.com/jjgod/mac-dictionary-kit) — open-source clone of Apple's Dictionary Development Kit
- [zyronon/typing-word](https://github.com/zyronon/typing-word) — word frequency data
- [lemminflect](https://github.com/bjascob/LemmInflect), [NLTK WordNet](https://www.nltk.org/howto/wordnet.html) — morphological tools
