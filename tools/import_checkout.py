"""Import a local GitHub checkout into the catalog. Does not execute repository code."""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from uaskills.importer import import_github_checkout
from uaskills.library import Library


def main(argv: list[str]) -> int:
    if len(argv) not in {3, 4}:
        print("usage: python tools/import_checkout.py OWNER REPO CHECKOUT [SPDX]", file=sys.stderr)
        return 2
    owner, repo, checkout = argv[:3]
    root = Path(checkout)
    sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    library = Library()
    library.settings["limits"]["max_skills_per_repo"] = 2000
    library.settings["limits"]["max_files_per_skill"] = 400
    library.settings["limits"]["max_file_bytes"] = 2000000
    license_spdx = argv[3] if len(argv) == 4 else None
    report = import_github_checkout(
        library,
        root,
        owner,
        repo,
        commit_sha=sha,
        repo_url=f"https://github.com/{owner}/{repo}",
        license_spdx=license_spdx,
    )
    payload = report.as_dict()
    payload["commit_sha"] = sha
    print(json.dumps(payload, indent=2))
    library.close()
    return 0 if report.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
