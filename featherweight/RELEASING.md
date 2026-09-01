# Releasing Featherweight

Releases are signed, notarized DMGs built by `.github/workflows/release.yml`
on every `v*` tag, or locally with `./release.sh`.

## One-time setup

Requires a paid Apple Developer Program membership.

### 1. Developer ID certificate

1. Xcode → Settings → Accounts → your team → **Manage Certificates…** →
   **+** → **Developer ID Application** (or create it in the
   [developer portal](https://developer.apple.com/account/resources/certificates)).
2. Export it for CI: open **Keychain Access**, find
   `Developer ID Application: <Your Name> (<TEAMID>)`, right-click →
   **Export**, save as `.p12` with a strong password.
3. Base64-encode it: `base64 -i cert.p12 | pbcopy`

### 2. App Store Connect API key (for notarization)

1. [App Store Connect](https://appstoreconnect.apple.com) → **Users and
   Access** → **Integrations** → **App Store Connect API** → generate a
   **Team Key** with the **Developer** role.
2. Download the `.p8` file (downloadable only once) and note the
   **Key ID** and **Issuer ID** shown on that page.

### 3. GitHub repository secrets

Settings → Secrets and variables → Actions → **New repository secret**:

| Secret                        | Value                                                    |
| ----------------------------- | -------------------------------------------------------- |
| `CODESIGN_IDENTITY`           | `Developer ID Application: <Your Name> (<TEAMID>)`       |
| `DEVELOPER_ID_CERT_P12`       | base64 of the exported `.p12`                            |
| `DEVELOPER_ID_CERT_PASSWORD`  | the `.p12` export password                               |
| `ASC_KEY_P8`                  | full contents of the `.p8` API key file                  |
| `ASC_KEY_ID`                  | the API key's Key ID                                     |
| `ASC_ISSUER_ID`               | the API key's Issuer ID (UUID)                           |

Never commit any of these to the repository.

## Cutting a release

```sh
git tag v1.0.1
git push origin v1.0.1
```

The workflow runs tests, builds, signs with hardened runtime + timestamp,
creates the DMG, notarizes it (`notarytool --wait`), staples the ticket,
and attaches the DMG to a GitHub release.

## Local release build

```sh
export CODESIGN_IDENTITY="Developer ID Application: <Your Name> (<TEAMID>)"
export VERSION=1.0.1
export ASC_KEY_FILE=~/keys/AuthKey_XXXXXXXXXX.p8
export ASC_KEY_ID=XXXXXXXXXX
export ASC_ISSUER_ID=00000000-0000-0000-0000-000000000000
./release.sh
```

Without those variables, `./release.sh` still produces an ad-hoc-signed DMG
for local testing (Gatekeeper will block it on other Macs — that's expected).

## Verifying a shipped DMG

```sh
spctl -a -t open --context context:primary-signature -v Featherweight-<version>.dmg
xcrun stapler validate Featherweight-<version>.dmg
```
