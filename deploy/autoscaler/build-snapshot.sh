#!/bin/sh
# Prepare a fresh Debian 13 cloud server to become the golden render-worker snapshot.
# Run as root on that server, then power it off and snapshot it (docs/autoscaling.md).
#
#   build-snapshot.sh <app host IPv4> <app host IPv6 /64 or "-"> <admin.pub> <tunnel.pub> <worker-compose.yml>
#
# The snapshot holds no credentials and no release image: the autoscaler copies
# both to each new machine when it starts one.
set -eu

APP_IPV4=$1
APP_IPV6=$2
ADMIN_PUB=$3
TUNNEL_PUB=$4
COMPOSE=$5

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg ufw unattended-upgrades

# Docker Engine and the Compose plugin from Docker's repository.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $VERSION_CODENAME stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin
cat > /etc/docker/daemon.json <<'EOF'
{"log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "3"}}
EOF
systemctl enable docker

# Users: `supoclip` administers the machine (key only, passwordless sudo);
# `tunnel` may only open the reverse forwards the application host needs.
id supoclip >/dev/null 2>&1 || useradd -m -s /bin/bash supoclip
id tunnel >/dev/null 2>&1 || useradd -m -s /usr/sbin/nologin tunnel
install -d -m 700 -o supoclip -g supoclip /home/supoclip/.ssh
install -m 600 -o supoclip -g supoclip "$ADMIN_PUB" /home/supoclip/.ssh/authorized_keys
install -d -m 700 -o tunnel -g tunnel /home/tunnel/.ssh
printf 'restrict,port-forwarding,permitopen="127.0.0.1:1",permitlisten="127.0.0.1:15432",permitlisten="127.0.0.1:16379",permitlisten="127.0.0.1:14416",command="/bin/false" %s\n' \
  "$(cat "$TUNNEL_PUB")" > /home/tunnel/.ssh/authorized_keys
chown tunnel:tunnel /home/tunnel/.ssh/authorized_keys
chmod 600 /home/tunnel/.ssh/authorized_keys
echo 'supoclip ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-supoclip
chmod 440 /etc/sudoers.d/90-supoclip

cat > /etc/ssh/sshd_config.d/10-supoclip.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers supoclip tunnel
X11Forwarding no
AllowAgentForwarding no
AllowTcpForwarding no
GatewayPorts no
PermitTunnel no
ClientAliveInterval 15
ClientAliveCountMax 4
Match User tunnel
  AllowTcpForwarding remote
EOF
sshd -t

# SSH from the application host only. The cloud firewall should say the same.
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow from "$APP_IPV4" to any port 22 proto tcp
[ "$APP_IPV6" = "-" ] || ufw allow from "$APP_IPV6" to any port 22 proto tcp
ufw --force enable

# Worker layout. Secrets and the compose env file arrive per machine.
install -d -m 755 /opt/supoclip-render
install -m 644 "$COMPOSE" /opt/supoclip-render/compose.yml
install -d -m 700 /etc/supoclip
install -d -m 755 /srv/supoclip-scratch /var/log/supoclip/worker-app

# Automatic security updates, no automatic reboots (a reboot would cut renders off).
cat > /etc/apt/apt.conf.d/52supoclip-unattended <<'EOF'
Unattended-Upgrade::Automatic-Reboot "false";
EOF

# Forget this machine's identity so each clone gets its own (the autoscaler
# supplies a host key it has already pinned through cloud-init).
cloud-init clean --logs --machine-id
rm -f /etc/ssh/ssh_host_*
truncate -s 0 /etc/machine-id
journalctl --rotate >/dev/null 2>&1 || true
journalctl --vacuum-time=1s >/dev/null 2>&1 || true
rm -f /root/.bash_history /home/supoclip/.bash_history
echo "Ready. Power off this server and create a snapshot labelled supoclip-render-golden=true."
