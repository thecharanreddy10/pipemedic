"""A short benchmark of the service: a fixed number of requests through the app, with a working set held in memory.

Run from the service folder. BENCH_MEM_MB sets the working set (default 40 MB), BENCH_REQUESTS the number of requests.
"""
import os
import sys
import time

sys.path.insert(0, os.getcwd())

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

mb = int(os.getenv("BENCH_MEM_MB", "40"))
requests = int(os.getenv("BENCH_REQUESTS", "3000"))

working_set = bytearray(mb * 1024 * 1024)
for offset in range(0, len(working_set), 4096):
    working_set[offset] = 1  # touch every page so the memory is really used

client = TestClient(app)
start = time.time()
for _ in range(requests):
    assert client.get("/health").status_code == 200
elapsed = time.time() - start
print(f"{requests} requests in {elapsed:.1f}s ({requests / elapsed:.0f} requests/s), working set {mb} MB")
