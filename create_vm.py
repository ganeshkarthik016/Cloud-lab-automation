
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

import yaml


BASE_IMAGE = Path(
    "/var/lib/libvirt/images/noble-server-cloudimg-amd64.img"
)
IMAGE_DIR = Path("/var/lib/libvirt/images")
SSH_KEY = Path.home() / ".ssh/id_ed25519.pub"


def run(command):
    print(f"\n$ {' '.join(map(str, command))}")
    subprocess.run(command, check=True)



def generate_config(name, ssh_public_key):
    app_path = Path(__file__).parent / "calculator_app.py"

    if not app_path.exists():
        raise FileNotFoundError(
            f"Calculator application not found: {app_path}"
        )

    app_code = app_path.read_text()

    app_yaml = "".join(
        "      " + line + "\n"
        for line in app_code.splitlines()
    )

    return f"""#cloud-config
hostname: {name}
manage_etc_hosts: true

users:
  - name: ganesh
    groups: [sudo]
    shell: /bin/bash
    sudo: ALL=(ALL) NOPASSWD:ALL
    lock_passwd: true
    ssh_authorized_keys:
      - {json.dumps(ssh_public_key)}

ssh_pwauth: false

package_update: true
packages:
  - python3-flask

write_files:
  - path: /opt/calculator/calculator_app.py
    permissions: '0755'
    owner: root:root
    content: |
{app_yaml}
  - path: /etc/systemd/system/calculator.service
    permissions: '0644'
    owner: root:root
    content: |
      [Unit]
      Description=Calculator Flask Application
      After=network.target

      [Service]
      WorkingDirectory=/opt/calculator
      ExecStart=/usr/bin/python3 /opt/calculator/calculator_app.py
      Restart=always
      User=root

      [Install]
      WantedBy=multi-user.target

runcmd:
  - mkdir -p /opt/calculator
  - systemctl daemon-reload
  - systemctl enable --now calculator.service
"""



def main():
    parser = argparse.ArgumentParser(
        description="Create a calculator VM using KVM/libvirt."
    )

    parser.add_argument("--name", required=True, help="VM name")
    parser.add_argument(
        "--memory", type=int, default=1024,
        help="RAM in MiB (default: 1024)"
    )
    parser.add_argument(
        "--cpu", type=int, default=1,
        help="Number of vCPUs (default: 1)"
    )
    parser.add_argument(
        "--disk", type=int, default=8,
        help="Disk size in GiB (default: 8)"
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Generate and validate cloud-init without creating a VM"
    )

    args = parser.parse_args()

    if not args.name.replace("-", "").replace("_", "").isalnum():
        parser.error("VM name may contain only letters, numbers, - and _")

    if min(args.memory, args.cpu, args.disk) <= 0:
        parser.error("Memory, CPU and disk size must be positive")

    if not BASE_IMAGE.exists():
        sys.exit(f"Base image not found: {BASE_IMAGE}")

    if not SSH_KEY.exists():
        sys.exit(f"SSH public key not found: {SSH_KEY}")

    result = subprocess.run(
        ["virsh", "dominfo", args.name],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        sys.exit(f"VM '{args.name}' already exists.")

    disk_path = IMAGE_DIR / f"{args.name}.qcow2"
    seed_path = IMAGE_DIR / f"{args.name}-seed.iso"

    if disk_path.exists() or seed_path.exists():
        sys.exit("Disk or seed ISO already exists. Refusing to overwrite.")

    work_dir = (
        Path.home() / "cloud_lab_automation" / "build" / args.name
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    user_data_path = work_dir / "user-data"
    meta_data_path = work_dir / "meta-data"
    local_seed = work_dir / "seed.iso"

    ssh_public_key = SSH_KEY.read_text().strip()

    user_data = generate_config(args.name, ssh_public_key)

    meta_data = f"""instance-id: {args.name}-{uuid.uuid4().hex[:8]}
local-hostname: {args.name}
"""

    user_data_path.write_text(user_data)
    meta_data_path.write_text(meta_data)

    # Validate generated cloud-init YAML.
    config = yaml.safe_load(user_data_path.read_text())

    if not isinstance(config, dict):
        sys.exit("Invalid cloud-init configuration.")

    if config.get("hostname") != args.name:
        sys.exit("Cloud-init hostname validation failed.")

    if len(config.get("write_files", [])) != 2:
        sys.exit("Expected two cloud-init write_files entries.")

    print("\nCloud-init YAML validation: PASSED")
    print("Hostname:", config["hostname"])
    print("Packages:", config["packages"])
    print("Configured files:", len(config["write_files"]))

    if args.validate_only:
        print("\nValidation-only mode: no VM or disk was created.")
        print("Generated files:", work_dir)
        return

    print(f"\n[1/5] Creating disk for {args.name}...")

    run([
        "sudo", "qemu-img", "create",
        "-f", "qcow2",
        "-F", "qcow2",
        "-b", str(BASE_IMAGE),
        str(disk_path),
        f"{args.disk}G"
    ])

    print("\n[2/5] Creating cloud-init seed...")

    run([
        "cloud-localds",
        str(local_seed),
        str(user_data_path),
        str(meta_data_path)
    ])

    print("\n[3/5] Preparing libvirt image permissions...")

    run([
        "sudo", "install",
        "-o", "libvirt-qemu",
        "-g", "kvm",
        "-m", "0640",
        str(local_seed),
        str(seed_path)
    ])

    run([
        "sudo", "chown",
        "libvirt-qemu:kvm",
        str(disk_path)
    ])

    run([
        "sudo", "chmod", "0640",
        str(disk_path)
    ])

    print(f"\n[4/5] Creating and starting VM {args.name}...")

    run([
        "sudo", "virt-install",
        "--name", args.name,
        "--memory", str(args.memory),
        "--vcpus", str(args.cpu),
        "--import",
        "--os-variant", "ubuntu24.04",
        "--disk", f"path={disk_path},format=qcow2,bus=virtio",
        "--disk", f"path={seed_path},device=cdrom",
        "--network", "network=default,model=virtio",
        "--graphics", "none",
        "--console", "pty,target_type=serial",
        "--noautoconsole"
    ])

    print("\n[5/5] VM creation command completed.")
    print("VM name:", args.name)
    print("Check its state using:")
    print("  virsh list --all")
    print(f"  virsh domifaddr {args.name}")
    print("SSH command after IP is available:")
    print("  ssh ganesh@<VM-IP>")


if __name__ == "__main__":
    main()