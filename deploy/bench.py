"""简易并发压测：/login 页面 QPS。用法: python bench.py [total_requests] [concurrency]"""
import sys
import threading
import time
import http.client
from concurrent.futures import ThreadPoolExecutor

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
CONC = int(sys.argv[2]) if len(sys.argv) > 2 else 100
HOST, PORT = "127.0.0.1", 8888

lock = threading.Lock()
ok = 0
fail = 0
latencies = []


def worker(n):
    global ok, fail
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            conn = http.client.HTTPConnection(HOST, PORT, timeout=10)
            conn.request("GET", "/login")
            resp = conn.getresponse()
            resp.read()
            conn.close()
            good = resp.status == 200
        except Exception:
            good = False
        dt = time.perf_counter() - t0
        with lock:
            if good:
                ok += 1
                latencies.append(dt)
            else:
                fail += 1


per = TOTAL // CONC
start = time.perf_counter()
with ThreadPoolExecutor(max_workers=CONC) as ex:
    list(ex.map(worker, [per] * CONC))
elapsed = time.perf_counter() - start

latencies.sort()
p50 = latencies[len(latencies) // 2] * 1000 if latencies else 0
p95 = latencies[int(len(latencies) * 0.95)] * 1000 if latencies else 0
print(f"total={TOTAL} ok={ok} fail={fail} elapsed={elapsed:.2f}s qps={ok/elapsed:.1f} p50={p50:.1f}ms p95={p95:.1f}ms")
