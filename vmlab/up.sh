#!/usr/bin/env bash
# Starts the VM lab's machines: three Ubuntu 24.04 guests under KVM, on libvirt's NAT network, each with
# a fixed address. Ansible (vmlab/ansible) then turns them into a kubeadm cluster.
#
# Meant for a throwaway CI runner: it uses sudo, and opens the runner's forwarding for the VM network.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
out=${OUT:-$here/out}
images=${IMAGES:-/var/lib/libvirt/images/blindspot}
disk_gb=${DISK_GB:-20}

IMAGE_URL=https://cloud-images.ubuntu.com/releases/noble/release-20261017/ubuntu-24.04-server-cloudimg-amd64.img
IMAGE_SHA256=04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94

# name, address, MAC, vCPUs, memory in MiB. The names match the kind lab's, so the workload runs unchanged.
NODES=(
  "blindspot-control-plane 192.168.122.10 52:54:00:b1:00:10 2 4096"
  "blindspot-worker 192.168.122.11 52:54:00:b1:00:11 2 4096"
  "blindspot-worker2 192.168.122.12 52:54:00:b1:00:12 2 4096"
)

[ -w /dev/kvm ] || { echo "no usable /dev/kvm on this runner, so the VM lab can't run here"; exit 1; }
mkdir -p "$out"
sudo mkdir -p "$images"

# The runner's Docker sets the FORWARD policy to DROP, which would cut the VMs off from the internet.
sudo iptables -P FORWARD ACCEPT
if sudo iptables -L DOCKER-USER -n >/dev/null 2>&1; then
  sudo iptables -I DOCKER-USER -i virbr0 -j ACCEPT
  sudo iptables -I DOCKER-USER -o virbr0 -j ACCEPT
fi
# Just after install, libvirt may still be starting this network itself, so "already active" is fine here.
sudo virsh net-start default 2>/dev/null || true
for _ in $(seq 1 30); do
  sudo virsh net-info default | grep -Eq 'Active: +yes' && break
  sleep 1
done
sudo virsh net-info default | grep -Eq 'Active: +yes' || { echo "libvirt's default network never became active"; exit 1; }

[ -f "$out/id_ed25519" ] || ssh-keygen -q -t ed25519 -N "" -f "$out/id_ed25519"
base="$images/noble.img"
if ! sudo test -f "$base"; then
  curl -fsSLo "$out/noble.img" "$IMAGE_URL"
  echo "$IMAGE_SHA256  $out/noble.img" | sha256sum -c
  sudo mv "$out/noble.img" "$base"
fi

{
  echo "[control_plane]"
  echo "blindspot-control-plane ansible_host=192.168.122.10"
  echo "[workers]"
  echo "blindspot-worker ansible_host=192.168.122.11"
  echo "blindspot-worker2 ansible_host=192.168.122.12"
  echo "[all:vars]"
  echo "ansible_user=blindspot"
  echo "ansible_ssh_private_key_file=$out/id_ed25519"
  echo "ansible_ssh_common_args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null'"
  echo "kubeconfig_out=$out/kubeconfig"
} > "$out/inventory.ini"

for node in "${NODES[@]}"; do
  read -r name ip mac vcpus memory <<<"$node"
  entry="<host mac='$mac' name='$name' ip='$ip'/>"
  # Remove any entry left by an earlier lab on this machine, so a second run doesn't fail here.
  sudo virsh net-update default delete ip-dhcp-host "$entry" --live --config >/dev/null 2>&1 || true
  sudo virsh net-update default add ip-dhcp-host "$entry" --live --config
  sudo qemu-img create -q -f qcow2 -F qcow2 -b "$base" "$images/$name.qcow2" "${disk_gb}G"
  {
    echo "#cloud-config"
    echo "hostname: $name"
    echo "users:"
    echo "  - name: blindspot"
    echo "    sudo: ALL=(ALL) NOPASSWD:ALL"
    echo "    shell: /bin/bash"
    echo "    ssh_authorized_keys:"
    echo "      - $(cat "$out/id_ed25519.pub")"
  } > "$out/$name.user-data"
  printf 'instance-id: %s\nlocal-hostname: %s\n' "$name" "$name" > "$out/$name.meta-data"
  sudo virt-install --name "$name" --vcpus "$vcpus" --memory "$memory" --import \
    --disk "path=$images/$name.qcow2,format=qcow2" --network "network=default,mac=$mac" \
    --cloud-init "user-data=$out/$name.user-data,meta-data=$out/$name.meta-data" \
    --osinfo detect=on,require=off --graphics none --noautoconsole
done

for node in "${NODES[@]}"; do
  read -r name ip _ <<<"$node"
  for _ in $(seq 1 90); do
    if ssh -q -i "$out/id_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=3 "blindspot@$ip" true 2>/dev/null; then
      echo "$name is up at $ip"
      continue 2
    fi
    sleep 5
  done
  echo "$name never answered on SSH"
  exit 1
done
