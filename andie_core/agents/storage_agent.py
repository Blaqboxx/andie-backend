import os
import subprocess

def run():
    print("[StorageAgent] Checking SSD mount...")
    ssd_path = "/mnt/andie_storage/ssd"
    if not os.path.ismount(ssd_path):
        print(f"[StorageAgent] SSD not mounted at {ssd_path}. Attempting to remount...")
        # Try to remount (requires proper /etc/fstab entry)
        try:
            subprocess.run(["mount", ssd_path], check=True)
            print(f"[StorageAgent] Remounted {ssd_path}")
        except Exception as e:
            print(f"[StorageAgent] Remount failed: {e}")
    else:
        print(f"[StorageAgent] SSD mounted at {ssd_path}")
