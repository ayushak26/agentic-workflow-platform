#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this once as root on a fresh Ubuntu 24.04 IONOS VPS." >&2
  exit 1
fi

deploy_user=${DEPLOY_USER:-eurskem-deploy}
app_group=eurskem-app
app_gid=10001

apt-get update
apt-get install -y ca-certificates curl fail2ban rsync ufw
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

. /etc/os-release
architecture=$(dpkg --print-architecture)
echo \
  "deb [arch=${architecture} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker fail2ban

if ! id "$deploy_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$deploy_user"
fi
existing_app_group=$(getent group "$app_gid" | cut -d: -f1 || true)
if [[ -n ${existing_app_group} ]]; then
  app_group=${existing_app_group}
else
  groupadd --gid "$app_gid" "$app_group"
fi
usermod -aG docker "$deploy_user"
usermod -aG "$app_group" "$deploy_user"

install -d -m 0750 -o "$deploy_user" -g "$deploy_user" \
  /opt/eurskem \
  /opt/eurskem/releases \
  /opt/eurskem/shared \
  /opt/eurskem/backups
install -d -m 2770 -o "$deploy_user" -g "$app_group" \
  /opt/eurskem/shared/workflows

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

echo "Host setup complete."
echo "Also allow only SSH, 80/tcp, 443/tcp and 443/udp in the IONOS firewall policy."
echo "Add the GitHub deployment public key to /home/${deploy_user}/.ssh/authorized_keys."
