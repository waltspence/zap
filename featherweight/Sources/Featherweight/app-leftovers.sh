#!/bin/bash
# app-leftovers.sh — find orphaned Application Support dirs
# Called by Featherweight. Outputs du-style lines: KB\tpath
#
# Detection strategy per directory:
#  1. Skip whitelisted Apple/system identifiers
#  2. Extract significant tokens from dir name (company/product names, ignoring
#     common suffixes like "Desktop", "Sandbox Helper", version numbers)
#  3. Check if any installed .app name or bundle ID contains those tokens
#  4. If nothing matches, it's a candidate orphan

set -euo pipefail

# ── Whitelist (extended) ─────────────────────────────────────────
SYSTEM_IDS="\
com.apple\$|\
Caches\$|FileProvider\$|CloudDocs\$|AddressBook\$|CallHistoryDB\$|\
networkserviceproxy\$|Animoji\$|Knowledge\$|CoreFollowUp\$|icdd\$|DiskImages\$|Grab\$|\
CallHistoryTransactions\$|SyncServices\$|Quick Look\$|iCloud\$|accountsd\$|assistantd\$|\
authkit\$|BackgroundAssets\$|com.apple.siri\$|Containers\$|CoreParsec\$|CrashReporter\$|\
DifferentialPrivacy\$|FaceTime\$|GameKit\$|homeenergyd\$|identityservicesd\$|icloud\$|\
IMDPersistence\$|InstallProgress\$|keybagd\$|MediaStream\$|mstreamd\$|OAuth\$|personad\$|\
Powerlog\$|QuickLook\$|routined\$|Shortcuts\$|suggestd\$|Sylvan\$|TelephonyUtilities\$|Tips\$|\
trustd\$|TuriTrial\$|USDRenderer\$|wcd\$|WebKit\$|Spotlight\$|CoreML\$|\
com.apple.AuthenticationServices\$|com.apple.controlcenter\$|\
com.apple.iTunesCloud\$|com.apple.MediaPlayer\$|com.apple.ProtectedCloudStorage\$|\
com.apple.RemoteManagementAgent\$|com.apple.shazamd\$|\
com.apple.wallpaper\$|com.apple.siri.inference\$|com.apple.ap.promotedcontentd\$|\
com.apple.control-center.tips\$|com.apple.sbd\$|com.apple.sharedfilelist\$|\
com.apple.CloudDocs\$|com.apple.TCC\$|com.apple.assistant\$|\
com.apple.appstore\$|com.apple.geo\$|com.apple.parsecd\$|com.apple.parsec-fbf\$|\
com.apple.mediaanalysisd\$|com.apple.akd\$|com.apple.ap.adprivacyd\$|\
com.apple.avfoundation\$|com.apple.backgroundtaskmanagementagent\$|\
com.apple.biome\$|com.apple.cmfsyncagent\$|com.apple.contacts\$|\
com.apple.contextstored\$|com.apple.corespeech\$|com.apple.dmd\$|\
com.apple.familycontrols\$|com.apple.followup\$|com.apple.gamed\$|\
com.apple.helpviewer\$|com.apple.homed\$|com.apple.icloud.searchparty\$|\
com.apple.imagent\$|com.apple.imtransferagent\$|com.apple.kvs\$|\
com.apple.languageinstall\$|com.apple.macl\$|com.apple.mail\$|\
com.apple.metrickitd\$|com.apple.mobiletimer\$|com.apple.naturallanguaged\$|\
com.apple.newscore\$|com.apple.newsd\$|com.apple.notificationcenter\$|\
com.apple.parentalcontrols\$|com.apple.passd\$|com.apple.passkit\$|\
com.apple.paymentpassd\$|com.apple.peopled\$|com.apple.photoanalysisd\$|\
com.apple.photos\$|com.apple.preferencepanes\$|com.apple.printcenter\$|\
com.apple.quicklook.ui.helper\$|com.apple.recents\$|com.apple.reminders\$|\
com.apple.replayd\$|com.apple.safari\$|com.apple.screentime\$|\
com.apple.secinitd\$|com.apple.security\$|com.apple.sharingd\$|\
com.apple.sidecar\$|com.apple.siri.embeddedspeech\$|com.apple.siriknowledged\$|\
com.apple.softwareupdate\$|com.apple.speech.recognition\$|com.apple.stocks\$|\
com.apple.studentd\$|com.apple.syncdefaultsd\$|com.apple.triald\$|\
com.apple.universalaccess\$|com.apple.universalcontrol\$|com.apple.voiceservices\$|\
com.apple.weather\$|com.apple.weatherwidget\$|\
Dock\$|Chromium\$|clawdhub\$|tipsd\$|stickersd\$|privatecloudcomputed\$"

# ── Extract significant tokens from a directory name ─────────────
# e.g. "BraveSoftware" → "brave"
#      "Telegram Desktop" → "telegram"
#      "Sublime Text 3" → "sublimetext"
#      "Things Sandbox Helper" → "things"
#      "dashlane-cli" → "dashlane"
#      "com.openai.atlas" → "openai" "atlas"
#      "com.openai.chat" → "openai" "chat"
extract_tokens() {
    local raw="$1"

    # Split CamelCase/PascalCase on original case BEFORE lowering
    # "BraveSoftware" → "Brave Software" → then lowercase → "brave software"
    local split
    split=$(echo "$raw" | sed -E 's/([a-z])([A-Z])/\1 \2/g; s/([A-Z])([A-Z][a-z])/\1 \2/g')

    local lower
    lower=$(echo "$split" | tr '[:upper:]' '[:lower:]')

    # Strip common noise words (word-boundary aware, after CamelCase split)
    local cleaned
    cleaned=$(echo "$lower" \
        | sed -E 's/\b(sandbox|helper|desktop|cli|app|application|support|data|cache|software|inc|ltd|llc|corp|foundation|group|studio|labs|team)\b/ /g' \
        | sed -E 's/\bv[0-9]+\b//g; s/\b[0-9]+(\.?[0-9]+)*\b//g' \
        | sed -E 's/^com\.//' \
        | tr '.' ' ' | tr '-' ' ' | tr '_' ' ' \
        | tr -s ' ' | sed 's/^ *//;s/ *$//')

    echo "$cleaned"
}

# ── Build cached app info (name + bundle ID tokens) ──────────────
build_app_info() {
    local cache="$HOME/.cache/featherweight-app-info"
    if [ -f "$cache" ] && [ "$(find "$cache" -mmin -10 2>/dev/null)" ]; then
        cat "$cache"
        return
    fi
    mkdir -p "$(dirname "$cache")"
    > "$cache"

    for app in /Applications/*.app ~/Applications/*.app /System/Applications/*.app; do
        [ -d "$app" ] || continue
        local name bundle
        name=$(basename "$app" .app | tr '[:upper:]' '[:lower:]' | sed 's/ *$//')
        bundle=$(mdls -name kMDItemCFBundleIdentifier -raw "$app" 2>/dev/null | tr '[:upper:]' '[:lower:]' || true)
        echo "${name}|${bundle:-}"
    done >> "$cache"
    cat "$cache"
}

# ── Check if tokens match any installed app ──────────────────────
matches_installed_app() {
    local dir_tokens="$1"
    local app_info="$_app_info"

    for token in $dir_tokens; do
        [ ${#token} -lt 4 ] && continue  # skip tiny tokens

        while IFS='|' read -r app_name app_bundle; do
            [ -z "$app_name" ] && continue

            # Token in app name?
            if [[ "$app_name" == *"$token"* ]]; then return 0; fi

            # Token in bundle ID?
            if [ -n "$app_bundle" ] && [[ "$app_bundle" == *"$token"* ]]; then return 0; fi

            # App name in token? (for longer app names)
            if [ ${#app_name} -ge 5 ] && [[ "$token" == *"$app_name"* ]]; then return 0; fi
        done <<< "$app_info"
    done

    return 1
}

# ── Main scan ────────────────────────────────────────────────────
# Hoisted out of the loop: read the app-info cache once, and query brew
# once (each brew list invocation takes ~1s; per-directory calls add minutes).
_app_info=$(build_app_info)
_brew_available=0
_brew_formulas=""
_brew_casks=""
if command -v brew &>/dev/null; then
    _brew_available=1
    _brew_formulas=$(brew list --formula 2>/dev/null || true)
    _brew_casks=$(brew list --cask 2>/dev/null || true)
fi

for d in ~/Library/Application\ Support/*/; do
    dir_name=$(basename "$d")

    # 1. Whitelist check
    if echo "$dir_name" | grep -qE "$SYSTEM_IDS"; then
        continue
    fi

    # 2. Extract meaningful tokens
    tokens=$(extract_tokens "$dir_name")

    # 3. Check against installed apps
    if [ -n "$tokens" ] && matches_installed_app "$tokens"; then
        continue
    fi

    # 4. Conservative check: does a brew formula/cask exist?
    #    CLI tools and casks don't always have .app bundles.
    #    Err on the side of caution — if brew knows about it, keep it.
    #    NOTE: brew exits 141 (SIGPIPE) when grep -q closes the pipe after
    #    a match, which pipefail treats as failure. Use the hoisted lists to avoid.
    if [ "$_brew_available" = 1 ]; then
        for token in $tokens; do
            [ ${#token} -lt 4 ] && continue
            if echo "$_brew_formulas" | grep -qi "$token"; then continue 2; fi
            if echo "$_brew_casks" | grep -qi "$token"; then continue 2; fi
        done
    fi

    # No app found — candidate orphan
    du -sk "$d" 2>/dev/null
done | sort -rn | head -15
