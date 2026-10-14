#!/bin/bash
# One-time setup. Builds the Swift tools from source and creates hash-locked Python environments.
# Installs nothing system-wide; needs Xcode Command Line Tools and uv on PATH. No sudo.
set -euo pipefail
cd "$(dirname "$0")/.."
[ "$(id -u)" = 0 ] && { echo "Don't run setup as root."; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "Apple silicon only."; exit 1; }
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
command -v swift >/dev/null || { echo "Install the Xcode Command Line Tools first: xcode-select --install"; exit 1; }

echo "== Swift tools (sampler, power parser, Bench Monitor) from source"
swift build -c release --package-path sampler
(cd app/BenchMonitor && ./build.sh)

echo "== Python 3.12 environments (pinned versions, every wheel hash-checked)"
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes -r requirements/harness.txt
if [ "${1:-}" = "--voice" ]; then
  uv venv --python 3.12 .venv-voice
  uv pip install --python .venv-voice/bin/python --require-hashes -r requirements/voice.txt
fi

echo
echo "Done. Next: copy an example manifest to manifest.yaml, read it, then: .venv/bin/python -m bench.download"
echo "(add --voice to this script for the text-to-speech environment)"
