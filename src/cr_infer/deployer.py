import json
from typing import Dict, Any, Optional, List, Tuple
from cr_infer.client import GCPClient
from cr_infer.config import get_gpu_config

class CloudRunDeployer:
    def __init__(self, client: GCPClient):
        self.client = client

    def get_service(self, region: str, name: str) -> Optional[Dict[str, Any]]:
        url = f"https://run.googleapis.com/v2/projects/{self.client.project_id}/locations/{region}/services/{name}"
        response = self.client.session.get(url)
        if response.status_code == 200:
            return response.json()
        return None

    def list_services(self, region: str) -> List[Dict[str, Any]]:
        url = f"https://run.googleapis.com/v2/projects/{self.client.project_id}/locations/{region}/services"
        response = self.client.session.get(url)
        if response.status_code == 200:
            services = response.json().get("services", [])
            # Filter by label if possible, or just return all and let CLI filter
            return [s for s in services if s.get("labels", {}).get("managed-by") == "llm-manager" or s.get("labels", {}).get("managed-by") == "cr-infer"]
        return []

    def validate_vram(self, region: str, gpu_type: str, model_size_bytes: int) -> Tuple[bool, str]:
        """Check if GPU has enough VRAM for model."""
        gpu_config = get_gpu_config(region, gpu_type)
        if not gpu_config:
            return True, "" # Skip if unknown
        
        # Estimate: model_size * 1.2 (for overhead)
        est_vram_needed = (model_size_bytes / (1024**3)) * 1.2
        if gpu_config.vram_gb < est_vram_needed:
            return False, f"Model estimated to need ~{est_vram_needed:.1f}GB VRAM, but {gpu_type} only has {gpu_config.vram_gb}GB."
        return True, ""

    def deploy_service(
        self,
        name: str,
        region: str,
        image: str,
        model_id: str,
        bucket_name: str,
        gpu_type: str,
        framework: str,
        cpu: str = "8",
        memory: str = "16Gi",
        min_instances: int = 0,
        max_instances: int = 1,
        concurrency: int = 8,
        gpu_zonal_redundancy_disabled: bool = True,
        subnet: Optional[str] = None,
        network: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        args: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Deploy or update a Cloud Run service."""
        
        gpu_config = get_gpu_config(region, gpu_type)
        is_alpha = gpu_config.status == "Private Preview" if gpu_config else False
        
        mount_path = f"/gcs/{bucket_name}"
        if framework == "zml":
            mount_path = "/model"

        # Construct default env and args if not provided
        final_env = []
        if env_vars:
            final_env = [{"name": k, "value": v} for k, v in env_vars.items()]
        
        final_args = args or []

        if framework == "ollama" and not final_env:
            final_env = [
                {"name": "OLLAMA_MODELS", "value": f"{mount_path}/ollama/models"},
                {"name": "MODEL", "value": model_id},
                {"name": "OLLAMA_NUM_PARALLEL", "value": str(concurrency)},
            ]
        elif framework == "vllm" and not final_args:
            final_args = [
                f"--model={mount_path}/{model_id}",
                "--load-format=runai_streamer",
                "--tensor-parallel-size=1",
                "--port=8000",
                "--gpu-memory-utilization=0.8"
            ]
        
        container_port = 11434 if framework == "ollama" else 8000

        service_payload = {
            "template": {
                "containers": [{
                    "image": image,
                    "ports": [{"containerPort": container_port}],
                    "resources": {
                        "limits": {
                            "cpu": cpu,
                            "memory": memory,
                            "nvidia.com/gpu": "1"
                        }
                    },
                    "env": final_env,
                    "args": final_args,
                    "volumeMounts": [{"name": "gcs-bucket", "mount_path": mount_path}]
                }],
                "volumes": [{"name": "gcs-bucket", "gcs": {"bucket": bucket_name, "readOnly": True}}],
                "nodeSelector": {"accelerator": gpu_type},
                "scaling": {"minInstanceCount": min_instances, "maxInstanceCount": max_instances},
                "maxInstanceRequestConcurrency": concurrency,
                "gpuZonalRedundancyDisabled": gpu_zonal_redundancy_disabled
            },
            "labels": {"managed-by": "cr-infer"}
        }

        if is_alpha:
            service_payload["launchStage"] = "ALPHA"

        # Cloud Run V2 Direct VPC Egress
        if subnet:
            # We must use both network and subnetwork in the annotation
            interface = {"subnetwork": subnet}
            if network:
                interface["network"] = network

            service_payload["template"]["annotations"] = {
                "run.googleapis.com/network-interfaces": json.dumps([interface]),
                "run.googleapis.com/vpc-access-egress": "all-traffic"
            }

        existing = self.get_service(region, name)
        if existing:
            # Update
            url = f"https://run.googleapis.com/v2/projects/{self.client.project_id}/locations/{region}/services/{name}"
            # For update we use PATCH. We need to handle etag if we want to be safe.
            # But let's keep it simple for now.
            response = self.client.session.patch(url, json=service_payload)
        else:
            # Create
            url = f"https://run.googleapis.com/v2/projects/{self.client.project_id}/locations/{region}/services?serviceId={name}"
            response = self.client.session.post(url, json=service_payload)

        response.raise_for_status()
        return response.json()
