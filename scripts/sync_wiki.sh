#!/usr/bin/env bash
# ==============================================================================
# Fresnel Suite - GitHub Wiki Local Synchronization Tool
# Synchronizes the local wiki/ directory with the remote GitHub Wiki git repository
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WIKI_DIR="${REPO_ROOT}/wiki"
CACHE_DIR="${REPO_ROOT}/.wiki_cache"

DRY_RUN=0
FORCE=0

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Synchronizes markdown documentation in 'wiki/' with the remote GitHub Wiki repository.

Options:
    -n, --dry-run    Inspect diff and list changes without committing or pushing
    -f, --force      Clean existing cache and re-clone the remote wiki
    -h, --help       Display this help message and exit

Prerequisites:
    1. GitHub repository must have Wikis enabled (Settings > General > Features > Wikis).
    2. SSH credentials (or git credential helper) configured for GitHub write access.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n|--dry-run)
            DRY_RUN=1
            shift
            ;;
        -f|--force)
            FORCE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Error: Unknown argument '$1'" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ ! -d "${WIKI_DIR}" ]]; then
    echo "Error: Source wiki directory '${WIKI_DIR}' not found." >&2
    exit 1
fi

# Detect remote URL
ORIGIN_URL="$(git -C "${REPO_ROOT}" config --get remote.origin.url || true)"
if [[ -z "${ORIGIN_URL}" ]]; then
    echo "Error: Could not determine git remote.origin.url." >&2
    exit 1
fi

# Construct Wiki remote URL
# Example: git@github.com:owner/repo.git -> git@github.com:owner/repo.wiki.git
# Example: https://github.com/owner/repo.git -> https://github.com/owner/repo.wiki.git
if [[ "${ORIGIN_URL}" =~ ^(.*)\.git$ ]]; then
    WIKI_REMOTE="${BASH_REMATCH[1]}.wiki.git"
else
    WIKI_REMOTE="${ORIGIN_URL}.wiki.git"
fi

echo "=== Fresnel GitHub Wiki Sync ==="
echo "Source: ${WIKI_DIR}"
echo "Remote: ${WIKI_REMOTE}"

if [[ ${FORCE} -eq 1 && -d "${CACHE_DIR}" ]]; then
    echo "[-] Clearing cache directory: ${CACHE_DIR}..."
    rm -rf "${CACHE_DIR}"
fi

# Clone or pull wiki cache
if [[ ! -d "${CACHE_DIR}/.git" ]]; then
    echo "[1/3] Cloning remote wiki repository..."
    if [[ ${DRY_RUN} -eq 1 ]]; then
        echo "  (dry-run) Would clone ${WIKI_REMOTE} into ${CACHE_DIR}"
    else
        mkdir -p "${CACHE_DIR}"
        if ! git clone "${WIKI_REMOTE}" "${CACHE_DIR}"; then
            echo "" >&2
            echo "Error: Failed to clone wiki repository (${WIKI_REMOTE})." >&2
            echo "Hint: If the wiki was just enabled, create the first page (Home.md) in the" >&2
            echo "GitHub web UI to initialize the wiki git repository, then retry." >&2
            exit 1
        fi
    fi
else
    echo "[1/3] Updating local wiki cache..."
    if [[ ${DRY_RUN} -eq 1 ]]; then
        echo "  (dry-run) Would fetch and fast-forward ${CACHE_DIR}"
    else
        git -C "${CACHE_DIR}" fetch origin
        CURRENT_BRANCH="$(git -C "${CACHE_DIR}" rev-parse --abbrev-ref HEAD)"
        git -C "${CACHE_DIR}" reset --hard "origin/${CURRENT_BRANCH}"
    fi
fi

# Copy markdown files and assets
echo "[2/3] Mirroring wiki files..."
if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "  (dry-run) Comparing files in '${WIKI_DIR}' with '${CACHE_DIR}'..."
    if command -v rsync >/dev/null 2>&1 && [[ -d "${CACHE_DIR}/.git" ]]; then
        rsync -avun --delete --exclude='.git' "${WIKI_DIR}/" "${CACHE_DIR}/"
    else
        echo "  Local files ready to mirror:"
        find "${WIKI_DIR}" -type f -exec basename {} \;
    fi
else
    if command -v rsync >/dev/null 2>&1; then
        rsync -av --delete --exclude='.git' "${WIKI_DIR}/" "${CACHE_DIR}/"
    else
        # Fallback for systems without rsync
        find "${CACHE_DIR}" -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
        cp -R "${WIKI_DIR}/"* "${CACHE_DIR}/"
    fi
fi

# Commit and Push
echo "[3/3] Checking git diff..."
if [[ ${DRY_RUN} -eq 1 ]]; then
    echo "=== Dry Run Completed Successfully ==="
    exit 0
fi

cd "${CACHE_DIR}"
if [[ -z "$(git status --porcelain)" ]]; then
    echo "✨ Wiki is already up-to-date. No changes to commit."
    exit 0
fi

echo "Changes detected:"
git status --short

git add -A
COMMIT_USER="$(git -C "${REPO_ROOT}" config user.name || echo 'Fresnel Deployer')"
COMMIT_EMAIL="$(git -C "${REPO_ROOT}" config user.email || echo 'fresnel@users.noreply.github.com')"

git -c user.name="${COMMIT_USER}" -c user.email="${COMMIT_EMAIL}" \
    commit -m "docs(wiki): sync wiki documentation from main repository"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "Pushing changes to remote wiki (${CURRENT_BRANCH})..."
git push origin "${CURRENT_BRANCH}"

echo "✅ GitHub Wiki synchronized successfully!"
