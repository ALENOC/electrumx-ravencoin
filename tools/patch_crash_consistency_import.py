#!/usr/bin/env python3
from pathlib import Path

path = Path("electrumx/server/block_processor.py")
text = path.read_text()
old = "import traceback\nfrom collections import defaultdict\n"
new = "import traceback\nfrom array import array\nfrom collections import defaultdict\n"
if text.count(old) != 1:
    raise SystemExit(f"expected one block_processor import anchor, found {text.count(old)}")
path.write_text(text.replace(old, new, 1))

installer = Path("electrumx-ravencoin-install.py")
text = installer.read_text()
old = (
    '        "ELECTRUMX_ENABLED=true\\nELECTRUMX_RPC_HOST=127.0.0.1\\n"\n'
    '        "ELECTRUMX_RPC_PORT=8000\\nELECTRUMX_SSL_HOST=127.0.0.1\\n"\n'
)
new = (
    '        "ELECTRUMX_ENABLED=true\\nELECTRUMX_RPC_HOST=172.29.81.2\\n"\n'
    '        "ELECTRUMX_RPC_PORT=8001\\nELECTRUMX_SSL_HOST=electrumx\\n"\n'
)
if text.count(old) != 1:
    raise SystemExit(f"expected one generated monitor env anchor, found {text.count(old)}")
installer.write_text(text.replace(old, new, 1))
