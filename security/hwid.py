# security/hwid.py
import subprocess
import hashlib
import platform

def get_hardware_id() -> str:
    """Cihaza özel, değiştirilemez bir donanım kimliği (HWID) üretir."""
    hw_id = ""
    try:
        if platform.system() == "Windows":
            # Anakart seri numarasını al
            board_id = subprocess.check_output("wmic baseboard get serialnumber", shell=True).decode().split('\n')[1].strip()
            # C sürücüsü seri numarasını al
            disk_id = subprocess.check_output("wmic diskdrive get serialnumber", shell=True).decode().split('\n')[1].strip()
            hw_id = f"{board_id}-{disk_id}"
        else:
            hw_id = "UNKNOWN_DEVICE"
    except Exception:
        hw_id = "FALLBACK_DEVICE_ID"
    
    # SHA-256 ile hash'le (Gerçek veriyi sunucuya göndermemek için)
    return hashlib.sha256(hw_id.encode()).hexdigest()