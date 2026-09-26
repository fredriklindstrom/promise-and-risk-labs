#!/bin/bash
# Record that the currently staged tree passed a security review. Run only after the review.
# Usage: tools/mark-reviewed.sh "<one-line review summary>"
set -e
[ -n "$1" ] || { echo "usage: tools/mark-reviewed.sh \"<review summary>\""; exit 1; }
MARK="$(git rev-parse --git-dir)/security-review-ok"
printf '%s\n%s\n%s\n' "$(git write-tree)" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" > "$MARK"
echo "recorded review for staged tree $(head -1 "$MARK")"
