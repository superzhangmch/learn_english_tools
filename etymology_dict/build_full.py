#!/usr/bin/env python3.11
"""
Build macOS dictionary from etymology_dict.json and word_source.js.

Merges:
- Etymology (Chinese explanations) from etymology_dict.json
- Reference count (how many word lists include the word) from word_source.js

Generates AppleDict XML source, then user runs `make && make install`.
"""
import argparse
import json
import re
import html
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description="Build macOS Etymology Dict")
parser.add_argument("--data-dir", required=True,
                    help="Directory containing etymology_dict.json and word_source.js")
parser.add_argument("--output-dir", required=True,
                    help="Directory to write AppleDict source files into")
parser.add_argument("--ddk-path", required=True,
                    help="Path to the mac-dictionary-kit ddk/ directory (build tools)")
args = parser.parse_args()

DATA_DIR = Path(args.data_dir).expanduser().resolve()
OUTPUT_DIR = Path(args.output_dir).expanduser().resolve()
DDK_PATH = str(Path(args.ddk_path).expanduser().resolve())


def load_etymology():
    with open(DATA_DIR / "etymology_dict.json", encoding="utf-8") as f:
        return json.load(f)


def load_word_source():
    with open(DATA_DIR / "word_source.js", encoding="utf-8") as f:
        lines = f.readlines()

    # Parse word_source from line 1
    match = re.search(r"var word_source\s*=\s*({.*?});", lines[1])
    if not match:
        raise ValueError("Could not parse word_source")
    word_source = json.loads(match.group(1))

    # Extract ref count (first element of each array)
    ref_counts = {}
    for word, arr in word_source.items():
        if arr:
            ref_counts[word.lower()] = arr[0]

    return ref_counts


def escape_xml(text):
    """Escape text for XML content."""
    return html.escape(text, quote=True)


def get_lemma_candidates(word, lem):
    """Return all possible lemma forms for a word across POS tags."""
    candidates = set()
    for pos in ["n", "v", "a", "r"]:
        candidates.add(lem.lemmatize(word, pos=pos))
    return candidates


def adj_to_adv(adj):
    """Apply English orthography rules to derive -ly adverb from adjective."""
    if adj.endswith("ic"):
        return adj + "ally"           # basic -> basically
    if adj.endswith("ll"):
        return adj + "y"              # full -> fully
    if adj.endswith("le") and not adj.endswith("lle"):
        return adj[:-1] + "y"         # simple -> simply
    if adj.endswith("y") and len(adj) > 2 and adj[-2] not in "aeiou":
        return adj[:-1] + "ily"       # happy -> happily
    if adj.endswith("ue"):
        return adj[:-1] + "ly"        # true -> truly
    return adj + "ly"


def build_xml(etymology, ref_counts):
    """Generate AppleDict XML source."""
    from lemminflect import getAllInflections
    import nltk
    nltk.download("wordnet", quiet=True)
    nltk.download("omw-1.4", quiet=True)
    from nltk.corpus import wordnet as wn

    # Merge all unique words (case-insensitive key, preserve original case)
    all_words = {}

    for word in etymology:
        key = word.lower()
        if key not in all_words:
            all_words[key] = {"display": word, "aliases": set()}
        all_words[key]["etymology"] = etymology[word]

    # 1. Forward-generate inflections (plural/past/-ing/comparative)
    for key, info in all_words.items():
        try:
            infl = getAllInflections(key)
        except Exception:
            continue
        forms = set()
        for words in infl.values():
            forms.update(w.lower() for w in words)
        forms.discard(key)
        info["aliases"].update(forms)

    # 2. Generate -ly adverbs from adjectives (rule-based)
    for key, info in all_words.items():
        if any(s.pos() == "a" or s.pos() == "s" for s in wn.synsets(key)):
            adv = adj_to_adv(key)
            if adv != key:
                info["aliases"].add(adv)

    # 3. Add WordNet derivationally related forms (verb<->noun, adj<->noun, etc.)
    # IMPORTANT: only look at the lemma matching `key` itself — not all lemmas
    # in the synset. Otherwise synonyms (work~ferment) pollute each other's aliases.
    for key, info in all_words.items():
        derived = set()
        for syn in wn.synsets(key):
            for lemma in syn.lemmas():
                if lemma.name().lower() != key:
                    continue  # skip synonym lemmas
                for related in lemma.derivationally_related_forms():
                    name = related.name().lower().replace("_", " ")
                    if name and name != key and " " not in name:
                        derived.add(name)
        info["aliases"].update(derived)

    # Add ref_cnt: direct match first, then via inflection alias
    direct = 0
    via_alias = 0
    skipped = 0
    # Build reverse map: alias -> base word, so we can route ref_cnt
    alias_to_base = {}
    for key, info in all_words.items():
        for alias in info["aliases"]:
            # If multiple bases claim the same alias, prefer the one whose
            # alias set is smaller (more specific) — but for simplicity,
            # first-come wins. This is rare in practice.
            alias_to_base.setdefault(alias, key)

    for word_lower, cnt in ref_counts.items():
        if word_lower in all_words:
            all_words[word_lower]["ref_cnt"] = max(
                all_words[word_lower].get("ref_cnt", 0), cnt
            )
            direct += 1
        elif word_lower in alias_to_base:
            base = alias_to_base[word_lower]
            all_words[base]["ref_cnt"] = max(
                all_words[base].get("ref_cnt", 0), cnt
            )
            via_alias += 1
        else:
            skipped += 1

    print(f"Total etymology entries: {len(all_words)}")
    print(f"  With ref count: {sum(1 for v in all_words.values() if 'ref_cnt' in v)}")
    print(f"  ref_cnt direct match: {direct}")
    print(f"  ref_cnt via alias: {via_alias}")
    print(f"  word_source skipped: {skipped}")
    total_aliases = sum(len(v["aliases"]) for v in all_words.values())
    print(f"  Total alias index entries: {total_aliases}")

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<d:dictionary xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:d="http://www.apple.com/DTDs/DictionaryService-1.0.rng">',
    ]

    for i, (key, info) in enumerate(sorted(all_words.items())):
        if i % 5000 == 0:
            print(f"  Processing {i}/{len(all_words)}...")

        display = escape_xml(info["display"])
        entry_id = f"w{i}"

        # Build body HTML — use inline <span> elements like Apple's built-in
        # dictionaries, so content flows as a paragraph and wraps in the popup
        body_parts = [f'<span class="hwg"><span class="hw">{display} </span></span>']

        if "ref_cnt" in info:
            cnt = info["ref_cnt"]
            body_parts.append(f'<span class="ref">[{cnt}]</span> ')

        if "etymology" in info:
            ety = escape_xml(info["etymology"])
            body_parts.append(f'<span class="ety">{ety}</span>')

        body = "".join(body_parts)

        # Build all index entries: main word + lemma aliases
        index_tags = f'<d:index d:value="{display}"/>'
        for alias in sorted(info.get("aliases", set())):
            alias_esc = escape_xml(alias)
            index_tags += f'<d:index d:value="{alias_esc}"/>'

        lines.append(
            f'<d:entry id="{entry_id}" d:title="{display}">'
            f'{index_tags}'
            f'{body}'
            f'</d:entry>'
        )

    lines.append("</d:dictionary>")
    return "\n".join(lines)


def write_makefile():
    makefile = f'''DICT_NAME\t\t=\t"EtymologyDict"
DICT_SRC_PATH\t\t=\t"EtymologyDict.xml"
CSS_PATH\t\t=\t"EtymologyDict.css"
PLIST_PATH\t\t=\t"EtymologyDict.plist"

DICT_BUILD_OPTS\t\t=

DICT_BUILD_TOOL_DIR\t=\t{DDK_PATH}
DICT_BUILD_TOOL_BIN\t=\t$(DICT_BUILD_TOOL_DIR)

DICT_DEV_KIT_OBJ_DIR\t=\t./objects
export\tDICT_DEV_KIT_OBJ_DIR

DESTINATION_FOLDER\t=\t~/Library/Dictionaries
RM\t\t\t=\t/bin/rm

all:
\t"$(DICT_BUILD_TOOL_BIN)/build_dict.sh" $(DICT_BUILD_OPTS) $(DICT_NAME) $(DICT_SRC_PATH) $(CSS_PATH) $(PLIST_PATH)
\t@echo "Done."

install:
\t@echo "Installing into $(DESTINATION_FOLDER)".
\tmkdir -p $(DESTINATION_FOLDER)
\tditto --noextattr --norsrc $(DICT_DEV_KIT_OBJ_DIR)/$(DICT_NAME).dictionary $(DESTINATION_FOLDER)/$(DICT_NAME).dictionary
\ttouch $(DESTINATION_FOLDER)
\t@echo "Done."
\t@echo "To test the new dictionary, try Dictionary.app."

clean:
\t$(RM) -rf $(DICT_DEV_KIT_OBJ_DIR)
'''
    (OUTPUT_DIR / "Makefile").write_text(makefile)


def write_css():
    css = """
@charset "UTF-8";
.hw { font-size: 1.3em; font-weight: bold; color: #333; }
.ref { color: #888; font-size: 0.85em; }
.ety { line-height: 1.4; }
"""
    (OUTPUT_DIR / "EtymologyDict.css").write_text(css)


def write_plist():
    import plistlib
    plist = {
        "CFBundleDevelopmentRegion": "English",
        "CFBundleIdentifier": "com.zhangmiaochang.EtymologyDict",
        "CFBundleDisplayName": "Etymology Dict",
        "CFBundleName": "EtymologyDict",
        "CFBundleShortVersionString": "1.0",
        "DCSDictionaryManufacturerName": "Custom",
    }
    with open(OUTPUT_DIR / "EtymologyDict.plist", "wb") as f:
        plistlib.dump(plist, f)


def main():
    print("Loading etymology_dict.json...")
    etymology = load_etymology()
    print(f"  {len(etymology)} entries")

    print("Loading word_source.js...")
    ref_counts = load_word_source()
    print(f"  {len(ref_counts)} entries")

    print("Building XML...")
    xml_content = build_xml(etymology, ref_counts)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    xml_path = OUTPUT_DIR / "EtymologyDict.xml"
    print(f"Writing {xml_path}...")
    xml_path.write_text(xml_content, encoding="utf-8")
    print(f"  {len(xml_content)} chars written")

    print("Writing Makefile, CSS, plist...")
    write_makefile()
    write_css()
    write_plist()

    print(f"\nAll source files written to: {OUTPUT_DIR}")
    print(f"\nTo build and install:")
    print(f"  cd {OUTPUT_DIR} && make && make install")
    print(f"\nThen open Dictionary.app > Preferences > Enable 'Etymology Dict'")


if __name__ == "__main__":
    main()
