#!/usr/bin/env bash
# bump-version.sh — Bump QPDK version across all source files
#
# Usage:
#   ./scripts/bump-version.sh <new_version>
#   ./scripts/bump-version.sh <major|minor|patch>
#   ./scripts/bump-version.sh <new_version> --tag     # also create git tag
#   ./scripts/bump-version.sh <new_version> --commit   # also commit the change
#   ./scripts/bump-version.sh <new_version> --commit --tag  # commit + tag
#
# Examples:
#   ./scripts/bump-version.sh 0.2.0
#   ./scripts/bump-version.sh patch            # 0.1.0 -> 0.1.1
#   ./scripts/bump-version.sh minor --commit   # 0.1.0 -> 0.2.0 + git commit
#   ./scripts/bump-version.sh 1.0.0 --commit --tag  # set + commit + tag

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PYPROJECT="$PROJECT_ROOT/pyproject.toml"
INIT_PY="$PROJECT_ROOT/qpdk/__init__.py"

# --- Helpers ---

die() { echo "ERROR: $*" >&2; exit 1; }

get_current_version() {
    grep -oP '^version\s*=\s*"\K[^"]+' "$PYPROJECT" || die "Could not read version from $PYPROJECT"
}

validate_semver() {
    local ver="$1"
    if [[ ! "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?$ ]]; then
        die "Invalid semver: '$ver' (expected X.Y.Z or X.Y.Z-label)"
    fi
}

bump_component() {
    local current="$1" component="$2"
    local major minor patch
    IFS='.' read -r major minor patch <<< "${current%%-*}"

    case "$component" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "$major.$((minor + 1)).0" ;;
        patch) echo "$major.$minor.$((patch + 1))" ;;
        *) die "Unknown bump type: '$component' (use major, minor, or patch)" ;;
    esac
}

# --- Parse args ---

if [ $# -lt 1 ]; then
    echo "Usage: $0 <version|major|minor|patch> [--commit] [--tag]"
    echo ""
    echo "Current version: $(get_current_version)"
    exit 1
fi

NEW_VERSION="$1"
shift

DO_COMMIT=false
DO_TAG=false

while [ $# -gt 0 ]; do
    case "$1" in
        --commit) DO_COMMIT=true ;;
        --tag)    DO_TAG=true ;;
        *) die "Unknown flag: $1" ;;
    esac
    shift
done

CURRENT_VERSION="$(get_current_version)"

# Handle symbolic bump (major/minor/patch)
case "$NEW_VERSION" in
    major|minor|patch)
        NEW_VERSION="$(bump_component "$CURRENT_VERSION" "$NEW_VERSION")"
        ;;
esac

validate_semver "$NEW_VERSION"

if [ "$NEW_VERSION" = "$CURRENT_VERSION" ]; then
    echo "Version is already $CURRENT_VERSION — nothing to do."
    exit 0
fi

# --- Apply version ---

echo "Bumping version: $CURRENT_VERSION -> $NEW_VERSION"

# 1. pyproject.toml
if [ -f "$PYPROJECT" ]; then
    sed -i "s/^version = \"$CURRENT_VERSION\"/version = \"$NEW_VERSION\"/" "$PYPROJECT"
    echo "  Updated: pyproject.toml"
else
    die "Missing: $PYPROJECT"
fi

# 2. qpdk/__init__.py
if [ -f "$INIT_PY" ]; then
    sed -i "s/__version__ = \"$CURRENT_VERSION\"/__version__ = \"$NEW_VERSION\"/" "$INIT_PY"
    echo "  Updated: qpdk/__init__.py"
else
    die "Missing: $INIT_PY"
fi

# --- Verify ---

VERIFY_TOML="$(grep -oP '^version\s*=\s*"\K[^"]+' "$PYPROJECT")"
VERIFY_INIT="$(grep -oP '__version__\s*=\s*"\K[^"]+' "$INIT_PY")"

if [ "$VERIFY_TOML" != "$NEW_VERSION" ]; then
    die "Verification failed: pyproject.toml shows '$VERIFY_TOML' (expected '$NEW_VERSION')"
fi
if [ "$VERIFY_INIT" != "$NEW_VERSION" ]; then
    die "Verification failed: __init__.py shows '$VERIFY_INIT' (expected '$NEW_VERSION')"
fi

echo "  Verified: both files show $NEW_VERSION"

# --- Optional git operations ---

if $DO_COMMIT; then
    cd "$PROJECT_ROOT"
    git add "$PYPROJECT" "$INIT_PY"
    git commit -m "chore: bump version to $NEW_VERSION"
    echo "  Committed: version bump to $NEW_VERSION"
fi

if $DO_TAG; then
    if ! $DO_COMMIT; then
        echo "  WARNING: --tag without --commit — tagging current HEAD (uncommitted changes not included)"
    fi
    cd "$PROJECT_ROOT"
    git tag -a "v$NEW_VERSION" -m "Release v$NEW_VERSION"
    echo "  Tagged: v$NEW_VERSION"
fi

echo ""
echo "Done. Version is now $NEW_VERSION"
