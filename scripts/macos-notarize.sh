#!/usr/bin/env bash
# submit a signed .dmg/.zip to Apples notarytool, wait for accept, staple the ticket
#
# usage:
#   scripts/macos-notarize.sh "target/Emu68 Hatcher.dmg"
#
# env (one of these auth modes):
#   keychain profile (preferred, more reliable):
#     APPLE_NOTARY_PROFILE = name passed to `xcrun notarytool store-credentials` earlier
#   direct credentials (CI):
#     APPLE_ID       = email of the apple developer account
#     APPLE_TEAM_ID  = 10-char team identifier (e.g. D93P9WFZG6)
#     APPLE_APP_PWD  = app-specific password (NOT the apple id password)
set -euo pipefail

TARGET="${1:?path to .dmg or .zip required}"

if [[ ! -f "$TARGET" ]]; then
    echo "target not found: $TARGET" >&2
    exit 1
fi

if [[ -n "${APPLE_NOTARY_PROFILE:-}" ]]; then
    AUTH_ARGS=(--keychain-profile "$APPLE_NOTARY_PROFILE")
    echo "[notarize] auth: keychain profile '$APPLE_NOTARY_PROFILE'"
elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PWD:-}" ]]; then
    AUTH_ARGS=(--apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" --password "$APPLE_APP_PWD")
    echo "[notarize] auth: direct credentials"
else
    echo "[notarize] no auth: set APPLE_NOTARY_PROFILE or APPLE_ID + APPLE_TEAM_ID + APPLE_APP_PWD" >&2
    exit 1
fi

LOG=$(mktemp)
trap 'rm -f "$LOG"' EXIT

echo "[notarize] submitting $TARGET..."
# a non-zero submit still leaves the json in $LOG, so the status check below decides
xcrun notarytool submit "$TARGET" \
    "${AUTH_ARGS[@]}" \
    --wait \
    --timeout 30m \
    --output-format json | tee "$LOG" || true

STATUS=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("status", ""))' "$LOG" 2>/dev/null || true)
SUB_ID=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("id", ""))' "$LOG" 2>/dev/null || true)

if [[ -z "$STATUS" ]]; then
    echo "[notarize] no submission result, see output above" >&2
    exit 1
fi

if [[ "$STATUS" != "Accepted" ]]; then
    echo "[notarize] status: $STATUS - fetching log..."
    xcrun notarytool log "$SUB_ID" "${AUTH_ARGS[@]}"
    exit 1
fi

echo "[notarize] stapling ticket to $TARGET..."
xcrun stapler staple "$TARGET"
xcrun stapler validate "$TARGET"

echo "[notarize] done: $TARGET"
