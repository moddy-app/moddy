"""Replace Moddy emoji ids with the target bot's ids in any files.

Run on your own machine, in the other bot's repository, with the
``emoji_mapping.json`` produced by ``copy_emojis_jsk.py``:

    python remap_emoji_ids.py emoji_mapping.json locales/ cogs/ utils/emojis.py

Every ``.py``, ``.json`` and ``.md`` file under the given paths is rewritten in
place. Only exact known Moddy ids are replaced (snowflakes are unique), and the
emoji name in ``<:name:id>`` is kept as is (names are copied unchanged).
"""

import json
import pathlib
import re
import sys

SNOWFLAKE = re.compile(r"\d{17,20}")
EXTENSIONS = {".py", ".json", ".md"}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    mapping = {old: entry["new"] for old, entry in json.loads(pathlib.Path(sys.argv[1]).read_text()).items()}
    changed = 0
    for target in sys.argv[2:]:
        root = pathlib.Path(target)
        files = [root] if root.is_file() else [p for p in root.rglob("*") if p.suffix in EXTENSIONS]
        for path in files:
            text = path.read_text(encoding="utf-8")
            new = SNOWFLAKE.sub(lambda m: mapping.get(m.group(), m.group()), text)
            if new != text:
                path.write_text(new, encoding="utf-8")
                changed += 1
                print(f"updated {path}")
    print(f"{changed} file(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
