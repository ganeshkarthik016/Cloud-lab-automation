
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path


BASE_DIR = Path(__file__).parent
BACKEND_FILE = BASE_DIR / "backends.json"
CREATE_VM_SCRIPT = BASE_DIR / "create_vm.py"

LOAD_BALANCER_STATS = "http://localhost:8080/stats"

REQUEST_THRESHOLD = 10
CHECK_INTERVAL = 5
COOLDOWN_SECONDS = 120
MAX_INSTANCES = 3

VM_MEMORY = 1024
VM_CPU = 1
VM_DISK = 8

last_scale_time = 0


def get_stats():
    try:
        with urllib.request.urlopen(
            LOAD_BALANCER_STATS, timeout=5
        ) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError):
        print("Cannot reach load balancer. Retrying...")
        return None


def load_backends():
    with BACKEND_FILE.open() as file:
        return json.load(file)["backends"]


def save_backends(backends):
    temporary_file = BACKEND_FILE.with_suffix(".tmp")

    with temporary_file.open("w") as file:
        json.dump({"backends": backends}, file, indent=4)

    temporary_file.replace(BACKEND_FILE)


def get_existing_vm_names():
    result = subprocess.run(
        ["virsh", "list", "--all", "--name"],
        capture_output=True,
        text=True,
        check=True
    )

    return set(result.stdout.splitlines())


def get_vm_ip(vm_name, timeout=180):
    deadline = time.time() + timeout

    while time.time() < deadline:
        result = subprocess.run(
            ["virsh", "domifaddr", vm_name],
            capture_output=True,
            text=True
        )

        for line in result.stdout.splitlines():
            fields = line.split()

            for field in fields:
                if field.count(".") == 3 and "/" in field:
                    ip = field.split("/")[0]
                    if ip.startswith("192.168.122."):
                        return ip

        time.sleep(5)

    raise TimeoutError(f"Could not discover IP for {vm_name}")


def wait_for_service(ip, timeout=180):
    url = f"http://{ip}:5000/health"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3):
                return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(5)

    return False


def create_instance():
    global last_scale_time

    backends = load_backends()

    if len(backends) >= MAX_INSTANCES:
        print("Maximum instance limit reached.")
        return

    existing_vms = get_existing_vm_names()

    number = 1
    while f"calc-server-{number}" in existing_vms:
        number += 1

    vm_name = f"calc-server-{number}"

    print(f"\nScaling up: creating {vm_name}")

    command = [
        "python3",
        str(CREATE_VM_SCRIPT),
        "--name", vm_name,
        "--memory", str(VM_MEMORY),
        "--cpu", str(VM_CPU),
        "--disk", str(VM_DISK)
    ]

    result = subprocess.run(command)

    if result.returncode != 0:
        print("VM creation failed.")
        return

    print(f"Waiting for {vm_name} to obtain an IP...")

    try:
        ip = get_vm_ip(vm_name)
    except TimeoutError as error:
        print(error)
        return

    print(f"{vm_name} IP: {ip}")
    print("Waiting for calculator service...")

    if not wait_for_service(ip):
        print("Calculator service did not become reachable.")
        return

    new_backend = f"http://{ip}:5000"

    if new_backend not in backends:
        backends.append(new_backend)
        save_backends(backends)

    last_scale_time = time.time()

    print(f"Scaling successful: {vm_name} registered.")
    print(f"Active backends: {len(backends)}")


def main():
    global last_scale_time

    print("Cloud Lab Autoscaler")
    print(f"Request threshold: {REQUEST_THRESHOLD} in 30 seconds")
    print(f"Maximum instances: {MAX_INSTANCES}")

    while True:
        stats = get_stats()

        if stats is None:
            time.sleep(CHECK_INTERVAL)
            continue

        requests = stats["requests_in_window"]
        instances = len(load_backends())

        print(
            f"Requests in window: {requests} | "
            f"Active instances: {instances}"
        )

        cooldown_finished = (
            time.time() - last_scale_time >= COOLDOWN_SECONDS
        )

        if (
            requests >= REQUEST_THRESHOLD
            and instances < MAX_INSTANCES
            and cooldown_finished
        ):
            create_instance()

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
