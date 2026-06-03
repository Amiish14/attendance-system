#!/usr/bin/env python3
"""
One-shot downloader for face-api.js model weights.

Run this ONCE after cloning / setting up the project:

    python3 download_face_models.py

It fetches ~3 MB of weights into static/face-weights/ from a chain of mirrors
(GH-Pages first, unpkg/jsdelivr as fallbacks). After this runs, the browser
loads everything from your own server — no internet needed at runtime, no
"Models still loading" surprises from a blocked CDN.

Safe to re-run; existing files are skipped.
"""
from pathlib import Path
import sys
import urllib.request
import urllib.error


# Files face-api.js needs for: TinyFaceDetector, FaceLandmark68Net, FaceRecognitionNet.
# Each net = one JSON manifest + one or more binary shards.
FILES = [
    "tiny_face_detector_model-weights_manifest.json",
    "tiny_face_detector_model-shard1",
    "face_landmark_68_model-weights_manifest.json",
    "face_landmark_68_model-shard1",
    "face_recognition_model-weights_manifest.json",
    "face_recognition_model-shard1",
    "face_recognition_model-shard2",
]

MIRRORS = [
    "https://justadudewhohacks.github.io/face-api.js/weights",
    "https://raw.githubusercontent.com/justadudewhohacks/face-api.js/master/weights",
    "https://cdn.jsdelivr.net/gh/justadudewhohacks/face-api.js/weights",  # gh CDN serves repo files
]

DEST = Path(__file__).resolve().parent / "static" / "face-weights"


def fetch(url: str, dest: Path) -> int:
    """GET url → dest. Returns bytes written. Raises on failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "procam-attendance-setup/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        dest.write_bytes(data)
        return len(data)


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"Downloading face-api.js weights → {DEST}\n")

    ok, skipped, failed = 0, 0, []
    for name in FILES:
        out = DEST / name
        if out.exists() and out.stat().st_size > 0:
            print(f"  · {name:55} already present ({out.stat().st_size:,} bytes)")
            skipped += 1
            continue
        last_err = None
        for base in MIRRORS:
            try:
                size = fetch(f"{base}/{name}", out)
                print(f"  ✓ {name:55} {size:>10,} bytes  ({base.split('//')[1].split('/')[0]})")
                ok += 1
                break
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
                last_err = e
                continue
        else:
            print(f"  ✗ {name:55} FAILED — {last_err}")
            failed.append(name)

    print(f"\nDownloaded {ok} · skipped {skipped} · failed {len(failed)}")
    if failed:
        print("\nThese mirrors are unreachable from this network:")
        for m in MIRRORS:
            print(f"  · {m}")
        print("\nGrab the failed files manually and drop them into static/face-weights/,"
              " then rerun this script.")
        return 1
    print("\n✓ Face models are vendored locally. The app will load them from /static/face-weights/.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
