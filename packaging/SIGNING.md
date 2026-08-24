# Release signing

MarketStore POS release artifacts must be signed before they are published. The
GitHub Actions release workflow fails when signing credentials are missing, so an
unsigned package cannot accidentally be attached to a new release.

## Windows

Obtain a publicly trusted Authenticode code-signing certificate for the product
publisher. Add these GitHub Actions repository secrets:

- `WINDOWS_CERTIFICATE_BASE64`: Base64-encoded PKCS#12 (`.pfx`) certificate.
- `WINDOWS_CERTIFICATE_PASSWORD`: Password protecting the `.pfx` file.
- `WINDOWS_TIMESTAMP_URL`: Optional RFC 3161 timestamp URL. The workflow uses
  `http://timestamp.digicert.com` when it is omitted.

Encode a certificate on PowerShell without printing the private key:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("publisher.pfx")) |
  Set-Clipboard
```

New publicly trusted certificates are commonly stored in a hardware token or a
cloud HSM and cannot be exported as `.pfx`. In that case, replace the PFX signing
step with the certificate provider's official GitHub Action while keeping the
same three signing points: the desktop executable, web installer, and final NSIS
setup executable.

## macOS

Join the Apple Developer Program and create a `Developer ID Application`
certificate. Export it as a password-protected `.p12`, then add these repository
secrets:

- `MACOS_CERTIFICATE_BASE64`: Base64-encoded Developer ID `.p12` certificate.
- `MACOS_CERTIFICATE_PASSWORD`: Password protecting the `.p12` file.
- `MACOS_SIGNING_IDENTITY`: Full certificate identity, for example
  `Developer ID Application: Company Name (TEAMID)`.
- `APPLE_ID`: Apple Developer account email.
- `APPLE_TEAM_ID`: Apple Developer Team ID.
- `APPLE_APP_SPECIFIC_PASSWORD`: App-specific password created for notarization.

Encode the certificate on macOS:

```bash
base64 -i developer-id.p12 | pbcopy
```

The workflow imports the certificate into a temporary keychain, signs the app
with hardened runtime and a secure timestamp, sends the app and DMG to Apple's
notary service, staples the notarization ticket, and verifies Gatekeeper status.

Never commit `.pfx`, `.p12`, private keys, passwords, or Apple credentials to the
repository.
