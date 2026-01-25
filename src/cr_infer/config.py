from typing import List, Optional
from pydantic import BaseModel

class GpuConfig(BaseModel):
    name: str
    vram_gb: int
    accelerator: str
    status: str
    validCpus: List[str]
    validMemory: List[str]
    memory_bandwidth_gb_s: int

class RegionConfig(BaseModel):
    name: str
    description: str
    gpus: List[GpuConfig]

SUPPORTED_REGIONS: List[RegionConfig] = [
    RegionConfig(
        name="us-central1",
        description="Iowa",
        gpus=[
            GpuConfig(
                name="NVIDIA L4", vram_gb=24, accelerator="nvidia-l4", status="GA",
                validCpus=["8", "12", "16"], validMemory=["16Gi", "24Gi", "32Gi"], memory_bandwidth_gb_s=300
            ),
            GpuConfig(
                name="NVIDIA RTX 6000 Pro", vram_gb=96, accelerator="nvidia-rtx-pro-6000", status="Private Preview",
                validCpus=["20", "22", "24", "30"], validMemory=["80Gi", "88Gi", "96Gi", "120Gi"], memory_bandwidth_gb_s=1600
            ),
        ]
    ),
    RegionConfig(
        name="us-east4",
        description="Northern Virginia",
        gpus=[
            GpuConfig(
                name="NVIDIA L4", vram_gb=24, accelerator="nvidia-l4", status="GA",
                validCpus=["8", "12", "16"], validMemory=["16Gi", "24Gi", "32Gi"], memory_bandwidth_gb_s=300
            ),
            GpuConfig(
                name="NVIDIA H100", vram_gb=80, accelerator="nvidia-h100-80gb", status="Private Preview",
                validCpus=["20", "22", "24", "26"], validMemory=["80Gi", "160Gi", "240Gi", "360Gi"], memory_bandwidth_gb_s=3350
            ),
        ]
    ),
    RegionConfig(
        name="europe-west1",
        description="Belgium",
        gpus=[
            GpuConfig(
                name="NVIDIA L4", vram_gb=24, accelerator="nvidia-l4", status="GA",
                validCpus=["8", "12", "16"], validMemory=["16Gi", "24Gi", "32Gi"], memory_bandwidth_gb_s=300
            ),
        ]
    ),
    RegionConfig(
        name="europe-west4",
        description="Netherlands",
        gpus=[
            GpuConfig(
                name="NVIDIA L4", vram_gb=24, accelerator="nvidia-l4", status="GA",
                validCpus=["8", "12", "16"], validMemory=["16Gi", "24Gi", "32Gi"], memory_bandwidth_gb_s=300
            ),
            GpuConfig(
                name="NVIDIA RTX 6000 Pro", vram_gb=96, accelerator="nvidia-rtx-pro-6000", status="Private Preview",
                validCpus=["20", "22", "24", "30"], validMemory=["80Gi", "88Gi", "96Gi", "120Gi"], memory_bandwidth_gb_s=1600
            ),
        ]
    ),
    RegionConfig(
        name="asia-southeast1",
        description="Singapore",
        gpus=[
            GpuConfig(
                name="NVIDIA L4", vram_gb=24, accelerator="nvidia-l4", status="GA",
                validCpus=["8", "12", "16"], validMemory=["16Gi", "24Gi", "32Gi"], memory_bandwidth_gb_s=300
            ),
            GpuConfig(
                name="NVIDIA RTX 6000 Pro", vram_gb=96, accelerator="nvidia-rtx-pro-6000", status="Private Preview",
                validCpus=["20", "22", "24", "30"], validMemory=["80Gi", "88Gi", "96Gi", "120Gi"], memory_bandwidth_gb_s=1600
            ),
        ]
    ),
    RegionConfig(
        name="asia-south1",
        description="Delhi",
        gpus=[
            GpuConfig(
                name="NVIDIA L4", vram_gb=24, accelerator="nvidia-l4", status="GA",
                validCpus=["8", "12", "16"], validMemory=["16Gi", "24Gi", "32Gi"], memory_bandwidth_gb_s=300
            ),
        ]
    ),
    RegionConfig(
        name="asia-south2",
        description="Delhi",
        gpus=[
            GpuConfig(
                name="NVIDIA RTX 6000 Pro", vram_gb=96, accelerator="nvidia-rtx-pro-6000", status="Private Preview",
                validCpus=["20", "22", "24", "30"], validMemory=["80Gi", "88Gi", "96Gi", "120Gi"], memory_bandwidth_gb_s=1600
            ),
        ]
    ),
]

def get_region_config(region_name: str) -> Optional[RegionConfig]:
    for r in SUPPORTED_REGIONS:
        if r.name == region_name:
            return r
    return None

def get_gpu_config(region_name: str, accelerator: str) -> Optional[GpuConfig]:
    region = get_region_config(region_name)
    if not region:
        return None
    for g in region.gpus:
        if g.accelerator == accelerator:
            return g
    return None

def list_supported_regions() -> List[str]:
    return [r.name for r in SUPPORTED_REGIONS]

def list_supported_gpus(region_name: str) -> List[str]:
    region = get_region_config(region_name)
    if not region:
        return []
    return [g.accelerator for g in region.gpus]
