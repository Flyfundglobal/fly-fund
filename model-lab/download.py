"""Download the official brain-v1 release in resumable, verified ranges."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

from flybrain.data import FILES

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("FLY_DATA", ROOT / ".sites-runtime/flyai/data"))
RELEASE = "https://api.github.com/repos/alextitonis/fly.ai/releases/tags/brain-v1"
CHUNK = 128 * 1024


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(RELEASE, timeout=30) as response:
        assets = {a["name"]: a for a in json.load(response)["assets"]}
    for name, expected in FILES.items():
        target = DATA / name
        if target.exists() and digest(target) == expected:
            print(f"{name}: official SHA-256 verified", flush=True)
            continue
        asset = assets[name]
        with urllib.request.urlopen(urllib.request.Request(asset["url"], headers={"Accept": "application/octet-stream"}), timeout=30) as response:
            url = response.geturl()
        parts = DATA / (name + ".chunks")
        parts.mkdir(exist_ok=True)
        total = (asset["size"] + CHUNK - 1) // CHUNK

        def fetch(index):
            start = index * CHUNK
            end = min(asset["size"] - 1, start + CHUNK - 1)
            output = parts / f"{index:06d}"
            if output.exists() and output.stat().st_size == end - start + 1:
                return
            for attempt in range(4):
                try:
                    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
                    with urllib.request.urlopen(request, timeout=25) as response:
                        if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{asset['size']}":
                            raise RuntimeError("The server did not return the requested byte range")
                        block = response.read(CHUNK + 1)
                    if len(block) != end - start + 1:
                        raise RuntimeError("Incomplete byte range")
                    output.write_bytes(block)
                    return
                except Exception:
                    if attempt == 3:
                        raise RuntimeError(f"Could not download {name} chunk {index}") from None
                    time.sleep(attempt + 1)

        print(f"Downloading official {name}: {asset['size'] / 1e6:.1f} MB", flush=True)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=12) as pool:
            jobs = [pool.submit(fetch, i) for i in range(total)]
            for done, job in enumerate(as_completed(jobs), 1):
                job.result()
                if done % 128 == 0 or done == total:
                    print(f"{name}: {done}/{total} chunks, {time.perf_counter() - started:.1f}s", flush=True)
        joined = DATA / (name + ".verified-part")
        with joined.open("wb") as file:
            for i in range(total):
                file.write((parts / f"{i:06d}").read_bytes())
        if digest(joined) != expected:
            raise RuntimeError(f"{name}: SHA-256 mismatch; model will not be used")
        joined.replace(target)
        print(f"{name}: official SHA-256 verified", flush=True)


if __name__ == "__main__":
    main()
