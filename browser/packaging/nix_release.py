# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""nix/release.json: the release the Nix package (nix/package.nix) installs. Run by
publish.sh once the release is online, so `nix flake update` brings it to NixOS.

  uv run packaging/nix_release.py <tarball> <version> <repo> <nix/release.json>
"""
import base64
import hashlib
import json
import sys

tarball, version, repo, out = sys.argv[1:5]
digest = hashlib.sha256()
with open(tarball, "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        digest.update(chunk)
with open(out, "w") as f:
    json.dump({
        "version": version,
        "url": f"https://github.com/{repo}/releases/download/v{version}/mast-linux-x86_64.tar.xz",
        # Nix's form of the tarball's SHA-256 (the same one SHA256SUMS lists).
        "hash": "sha256-" + base64.b64encode(digest.digest()).decode(),
    }, f, indent=2)
    f.write("\n")
print(f"nix/release.json: {version}")
