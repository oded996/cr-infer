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

    def find_service(self, name: str) -> Optional[Tuple[Dict[str, Any], str]]:
        """Search for a service by name across all supported regions."""
        from cr_infer.config import list_supported_regions
        for region in list_supported_regions():
            svc = self.get_service(region, name)
            if svc:
                return svc, region
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

    def build_payload(
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
        """Construct the Cloud Run V2 service payload."""
        
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
                {"name": "OLLAMA_DEBUG", "value": "false"},
                {"name": "OLLAMA_KEEP_ALIVE", "value": "-1"}
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

        payload = {
            "template": {
                "containers": [{
                    "image": image,
                    "ports": [{"containerPort": container_port}],
                    "resources": {
                        "limits": {
                            "cpu": f"{cpu}",
                            "memory": memory,
                            "nvidia.com/gpu": "1"
                        }
                    },
                    "env": final_env,
                    "args": final_args,
                    "volumeMounts": [{"name": "gcs-bucket", "mount_path": mount_path}],
                    "startupProbe": {
                        "timeoutSeconds": 240,
                        "periodSeconds": 240,
                        "failureThreshold": 3,
                        "tcpSocket": {
                            "port": container_port
                        }
                    }
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
            payload["launchStage"] = "ALPHA"

        if subnet:
            interface = {"subnetwork": subnet}
            if network:
                interface["network"] = network

            payload["template"]["annotations"] = {
                "run.googleapis.com/network-interfaces": json.dumps([interface]),
                "run.googleapis.com/vpc-access-egress": "all-traffic"
            }
        
        return payload

    def deploy_service(
        self,
        name: str,
        region: str,
        payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute the deployment using a prepared payload."""
        existing = self.get_service(region, name)
        if existing:
            url = f"https://run.googleapis.com/v2/projects/{self.client.project_id}/locations/{region}/services/{name}"
            response = self.client.session.patch(url, json=payload)
        else:
            url = f"https://run.googleapis.com/v2/projects/{self.client.project_id}/locations/{region}/services?serviceId={name}"
            response = self.client.session.post(url, json=payload)

        if not response.ok:
            raise Exception(f"Cloud Run API Error {response.status_code}: {response.text}")
        return response.json()
