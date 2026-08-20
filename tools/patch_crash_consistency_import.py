#!/usr/bin/env python3
from pathlib import Path

path = Path("electrumx/server/block_processor.py")
text = path.read_text()
old = "import traceback\nfrom collections import defaultdict\n"
new = "import traceback\nfrom array import array\nfrom collections import defaultdict\n"
if text.count(old) != 1:
    raise SystemExit(f"expected one block_processor import anchor, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))
