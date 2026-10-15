"""Download listed paths from a local partial clone using raw.githubusercontent.com.

Does not execute the downloaded files.
"""

import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

checkout = Path(sys.argv[1])
prefix = sys.argv[2]
max_bytes = int(sys.argv[3]) if len(sys.argv) > 3 else 1_500_000
owner_repo = subprocess.check_output(
    ["git", "-C", str(checkout), "remote", "get-url", "origin"], text=True
).strip()
owner_repo = owner_repo.removesuffix(".git").split("github.com/")[-1]
sha = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
names = subprocess.check_output(
    ["git", "-C", str(checkout), "ls-tree", "-r", "--name-only", "HEAD", prefix], text=True
).splitlines()
dest_root = checkout
ok = 0
skipped = 0
for relative in names:
    relative = relative.strip()
    if not relative:
        continue
    if shutil.disk_usage(dest_root.resolve().anchor).free < 25 * 1024 * 1024:
        print(f"stopped: less than 40 MB free after {ok} files")
        break
    target = dest_root / relative
    if target.is_file() and target.stat().st_size > 0:
        ok += 1
        continue
    url = f"https://raw.githubusercontent.com/{owner_repo}/{sha}/{relative}"
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "universal-ai-skills"})
    response = None
    for attempt in range(4):
        try:
            response = urllib.request.urlopen(request, timeout=60)
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    with response:
        chunks = []
        used = 0
        too_big = False
        while True:
            block = response.read(64 * 1024)
            if not block:
                break
            used += len(block)
            if used > max_bytes:
                too_big = True
                break
            chunks.append(block)
    if too_big:
        skipped += 1
        print(f"skipped large file {relative}")
        continue
    target.write_bytes(b"".join(chunks))
    ok += 1
print(f"downloaded {ok} files, skipped {skipped}, at {sha}")
