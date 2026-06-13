#!/usr/bin/env bash
# install.sh — idempotent server installer for the FPV video-stream server side.
# Does NOT modify WireGuard / wg-easy. Only adds MediaMTX + dashboard.
set -euo pipefail

# ---- parameters (override via env or flags) ----
WG_IP="${WG_IP:-10.8.0.1}"
WG_IFACE="${WG_IFACE:-wg0}"
APP_DIR="${APP_DIR:-/opt/fpv-video-stream}"
MEDIAMTX_VERSION="${MEDIAMTX_VERSION:-latest}"
MEDIAMTX_CONFIG="/usr/local/etc/mediamtx.yml"

usage() { echo "Usage: sudo WG_IP=10.8.0.1 WG_IFACE=wg0 ./install.sh"; }
[ "${1:-}" = "-h" ] && { usage; exit 0; }

if [ "$(id -u)" -ne 0 ]; then echo "Run as root (sudo)."; exit 1; fi

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCH="$(dpkg --print-architecture)"  # amd64 / arm64
case "$ARCH" in amd64) MTX_ARCH=linux_amd64;; arm64) MTX_ARCH=linux_arm64v8;; *) echo "Unsupported arch $ARCH"; exit 1;; esac

echo "==> [1/8] Base packages (node, jq, curl, tar)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl tar jq ca-certificates >/dev/null
if ! command -v node >/dev/null || [ "$(node -v | cut -dv -f2 | cut -d. -f1)" -lt 18 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
echo "    node $(node -v)"

echo "==> [2/8] Service users"
id mediamtx >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin mediamtx
id fpv      >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin fpv

echo "==> [3/8] Install MediaMTX ($MEDIAMTX_VERSION, $MTX_ARCH)"
if [ "$MEDIAMTX_VERSION" = "latest" ]; then
  MEDIAMTX_VERSION="$(curl -fsSL https://api.github.com/repos/bluenviron/mediamtx/releases/latest | jq -r .tag_name)"
fi
if [ ! -x /usr/local/bin/mediamtx ] || ! /usr/local/bin/mediamtx --version 2>/dev/null | grep -q "${MEDIAMTX_VERSION#v}"; then
  TMP="$(mktemp -d)"
  curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_${MTX_ARCH}.tar.gz" -o "$TMP/m.tar.gz"
  tar -xzf "$TMP/m.tar.gz" -C "$TMP"
  install -m 0755 "$TMP/mediamtx" /usr/local/bin/mediamtx
  rm -rf "$TMP"
fi
echo "    $(/usr/local/bin/mediamtx --version 2>&1 | head -1)"
mkdir -p /usr/local/etc

echo "==> [4/8] App files -> $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$SRC_DIR/." "$APP_DIR/"
cd "$APP_DIR"

echo "==> [5/8] .env and devices.yml (created from examples if missing)"
if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s/^WG_IP=.*/WG_IP=${WG_IP}/" .env
  sed -i "s/^WG_IFACE=.*/WG_IFACE=${WG_IFACE}/" .env
  sed -i "s#^MEDIAMTX_CONFIG=.*#MEDIAMTX_CONFIG=${MEDIAMTX_CONFIG}#" .env
  sed -i "s/^DASH_HOST=.*/DASH_HOST=${WG_IP}/" .env
  sed -i "s/^SESSION_SECRET=.*/SESSION_SECRET=$(openssl rand -hex 24)/" .env
  sed -i "s/^DASH_PASS=.*/DASH_PASS=$(openssl rand -base64 12 | tr -d '/+=')/" .env
  echo "    generated .env (review DASH_USER/DASH_PASS)"
fi
[ -f devices.yml ] || { cp devices.example.yml devices.yml; echo "    seeded devices.yml from example — set real passwords or use add-device.sh"; }
chmod 600 .env devices.yml

echo "==> [6/8] npm install + render config"
set -a; . ./.env; set +a
npm install --omit=dev --no-audit --no-fund >/dev/null
node bin/gen-mediamtx.js
chown mediamtx:mediamtx "$MEDIAMTX_CONFIG"
chown -R fpv:fpv "$APP_DIR"

echo "==> [7/8] systemd units"
sed "s#/opt/fpv-video-stream#${APP_DIR}#g" systemd/fpv-dashboard.service > /etc/systemd/system/fpv-dashboard.service
cp systemd/mediamtx.service /etc/systemd/system/mediamtx.service
systemctl daemon-reload
systemctl enable --now mediamtx.service
systemctl enable --now fpv-dashboard.service

echo "==> [8/8] Firewall guidance (not enforced automatically)"
cat <<EOF
  Ports in use (all bound to ${WG_IP} except API on 127.0.0.1):
    8554/tcp  RTSP ingest      (${WG_IP})
    8890/udp  SRT ingest       (${WG_IP})
    8889/tcp  WebRTC/WHEP      (${WG_IP})
    8189/udp  WebRTC ICE       (advertises ${WG_IP})
    9997/tcp  Control API      (127.0.0.1 only)
    8080/tcp  Dashboard        (${WG_IP})
  Optional ufw (only allow these in via ${WG_IFACE}):
    ufw allow in on ${WG_IFACE} to any port 8554 proto tcp
    ufw allow in on ${WG_IFACE} to any port 8890 proto udp
    ufw allow in on ${WG_IFACE} to any port 8889 proto tcp
    ufw allow in on ${WG_IFACE} to any port 8189 proto udp
    ufw allow in on ${WG_IFACE} to any port 8080 proto tcp
  Do NOT expose these on the public interface. WireGuard handshake is the only public port.

Done. Dashboard: http://${WG_IP}:8080  (login from .env)
EOF
