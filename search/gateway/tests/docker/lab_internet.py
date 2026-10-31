"""The lab's "internet" for search-post-gate (runs in a container of the gate image):

    python lab_internet.py <pki dir> <intake key>

  :8888  FakeInternet, the plane's `vpn` stand-in (a forward proxy; CONNECT to dns.mullvad.net:443 is
         tunnelled to the HTTP/2-only DoH stand-in below, every other CONNECT is the stub web)
  :443   H2DohServer on 127.0.0.1 only (tests/h2_doh.py): Mullvad's measured shape
  :8000  FakeIntake, the screen stand-in the gate hands a fetched page to

A lab stand-in for the VPN container and for Mullvad; the real ones are probed read-only in
journal/evidence/search-post-gate/live/.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fakes import FakeIntake, FakeInternet  # noqa: E402
from h2_doh import DOH_HOST, H2DohServer  # noqa: E402

pki, key = Path(sys.argv[1]), sys.argv[2]
doh = H2DohServer(pki).start()
net = FakeInternet("0.0.0.0", 8888)
net.tunnels[f"{DOH_HOST}:443"] = ("127.0.0.1", doh.port)
net.start()
FakeIntake(key, "0.0.0.0", 8000).start()
print("lab internet up", flush=True)
threading.Event().wait()
