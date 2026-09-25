#!/bin/bash
# On-camera batching demo. Run inside a Python env with mlx-lm installed.
exec "${PYTHON:-python3}" "$(dirname "$0")/demo.py"
