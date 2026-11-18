#!/usr/bin/env bash
# One-time: create a self-signed code-signing identity in your login keychain so macOS keeps
# Glint's Accessibility/Microphone permissions across rebuilds (ad-hoc signatures change on
# every build, which makes macOS forget the grant). The key never leaves this Mac.
set -euo pipefail
NAME="${GLINT_SIGN_IDENTITY:-Glint Local Signing}"
if security find-certificate -c "$NAME" >/dev/null 2>&1; then
  echo "Signing identity '$NAME' already exists."; exit 0
fi
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/cfg" <<CFG
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
basicConstraints = critical, CA:false
CFG
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -config "$TMP/cfg" \
  -keyout "$TMP/key.pem" -out "$TMP/cert.pem" 2>/dev/null
PASS="$(openssl rand -hex 12)"
openssl pkcs12 -export -inkey "$TMP/key.pem" -in "$TMP/cert.pem" -name "$NAME" \
  -out "$TMP/id.p12" -passout "pass:$PASS" 2>/dev/null
security import "$TMP/id.p12" -k "$HOME/Library/Keychains/login.keychain-db" -P "$PASS" -T /usr/bin/codesign >/dev/null
echo "Created signing identity '$NAME' in your login keychain."
