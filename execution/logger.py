print("LOGGER MODULE LOADED", flush=True)
# execution/logger.py
from datetime import datetime

def log_info(message: str) -> None:
    print(f"[INFO] {datetime.utcnow().isoformat()}Z - {message}", flush=True)

def log_warning(message: str) -> None:
    print(f"[WARN] {datetime.utcnow().isoformat()}Z - {message}", flush=True)

def log_error(message: str) -> None:
    print(f"[ERROR] {datetime.utcnow().isoformat()}Z - {message}", flush=True)

