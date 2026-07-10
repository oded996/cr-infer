from typing import Dict, Any, Optional
from cr_infer.client import GCPClient

QUOTA_ID_MAP = {
    "nvidia-l4": {
        "non_zonal": "NvidiaL4GpuAllocNoZonalRedundancyPerProjectRegion",
        "zonal": "NvidiaL4GpuAllocPerProjectRegion"
    },
    "nvidia-rtx-pro-6000": {
        "non_zonal": "NvidiaRtxPro6000GpuAllocNoZonalRedundancyPerProjectRegion",
        "zonal": "NvidiaRtxPro6000GpuAllocPerProjectRegion"
    }
}

def get_regional_value(quota_info: Dict[str, Any], region: str) -> int:
    """Extract regional value from quota info dimensions."""
    if not quota_info or "dimensionsInfos" not in quota_info:
        return 0
    for info in quota_info["dimensionsInfos"]:
        if info.get("dimensions", {}).get("region") == region:
            return int(info.get("details", {}).get("value", 0))
    return 0

def normalize_value(gpu_accelerator: str, value: int) -> float:
    """Normalize values (e.g. RTX 6000 is in milli-units)."""
    if gpu_accelerator == "nvidia-rtx-pro-6000":
        return value / 1000.0
    return float(value)

def fetch_gpu_quota(client: GCPClient, region: str, gpu_type: str) -> Dict[str, float]:
    """Fetch both zonal and non-zonal quotas for a GPU type in a region."""
    from cr_infer.config import get_gpu_config
    gpu_cfg = get_gpu_config(region, gpu_type)
    accelerator = gpu_cfg.accelerator if gpu_cfg else gpu_type

    ids = QUOTA_ID_MAP.get(accelerator)
    if not ids:
        raise ValueError(f"Unsupported GPU type: {gpu_type}")

    non_zonal_info = client.get_quota_info(region, ids["non_zonal"])
    zonal_info = client.get_quota_info(region, ids["zonal"])

    non_zonal_limit = get_regional_value(non_zonal_info, region)
    zonal_limit = get_regional_value(zonal_info, region)

    return {
        "non_zonal": normalize_value(accelerator, non_zonal_limit),
        "zonal": normalize_value(accelerator, zonal_limit)
    }
