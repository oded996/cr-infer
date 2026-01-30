import requests
import json
from typing import Dict, Any, Optional
from cr_infer.client import GCPClient

METADATA_FILE_NAME = "llm-manager-metadata.json"

def hf_preflight(model_id: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Check Hugging Face model existence and get total size."""
    url = f"https://huggingface.co/api/models/{model_id}?blobs=true"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    
    response = requests.get(url, headers=headers)
    if response.status_code in [401, 403]:
        raise PermissionError(f"Hugging Face model {model_id} is gated or requires authentication.")
    if response.status_code != 200:
        raise Exception(f"Hugging Face model {model_id} not found or inaccessible.")
    
    data = response.json()
    is_gated = data.get("gated")
    
    # Try multiple ways to find the model size
    # 1. 'usedStorage' (often present for large models/LFS)
    total_size = data.get("usedStorage") or 0
        
    # 2. 'safetensors' metadata (most accurate for weights)
    if total_size == 0:
        safetensors = data.get("safetensors")
        if isinstance(safetensors, dict):
            total_size = safetensors.get("total") or 0
    
    # 3. Sum of siblings (accurate when blobs=true is used)
    if total_size == 0:
        for sibling in data.get("siblings", []):
            total_size += sibling.get("size") or 0
            
    # 4. Top-level 'size' field
    if total_size == 0:
        total_size = data.get("size") or 0
    
    # If it's gated and we still have no size, it's highly likely we need a token
    if is_gated and total_size == 0 and not token:
        raise PermissionError(f"Hugging Face model {model_id} is gated and requires a token to access metadata.")
        
    return {
        "model_id": model_id,
        "source": "huggingface",
        "exists": True,
        "total_size": total_size if total_size > 0 else None,
        "gated": is_gated
    }

def ollama_preflight(model_id: str) -> Dict[str, Any]:
    """Check Ollama model existence via registry.ollama.ai."""
    # Ollama models usually follow 'library/name:tag' format
    parts = model_id.split(':')
    name = parts[0]
    tag = parts[1] if len(parts) > 1 else "latest"
    if '/' not in name:
        name = f"library/{name}"
    
    # Get manifest to check existence and size
    url = f"https://registry.ollama.ai/v2/{name}/manifests/{tag}"
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Ollama model {model_id} not found.")
    
    manifest = response.json()
    total_size = sum(layer.get("size", 0) for layer in manifest.get("layers", []))
    total_size += manifest.get("config", {}).get("size", 0)
    
    return {
        "model_id": model_id,
        "source": "ollama",
        "exists": True,
        "total_size": total_size,
        "manifest": manifest
    }

def list_models_in_bucket(client: GCPClient, bucket_name: str) -> list:
    """List models tracked in the bucket's metadata file."""
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o/{METADATA_FILE_NAME}?alt=media"
    try:
        response = client.session.get(url)
        if response.status_code == 200:
            return response.json().get("models", [])
        return []
    except Exception:
        return []

def update_metadata(client: GCPClient, bucket_name: str, model_data: Dict[str, Any]):
    """Add or update model entry in the GCS metadata file."""
    # 1. Fetch current metadata
    url = f"https://storage.googleapis.com/storage/v1/b/{bucket_name}/o/{METADATA_FILE_NAME}?alt=media"
    metadata = {"description": "Managed by cr-infer", "models": []}
    response = client.session.get(url)
    if response.status_code == 200:
        metadata = response.json()
    
    # 2. Update model list
    models = [m for m in metadata.get("models", []) if m["id"] != model_data["id"]]
    models.append(model_data)
    metadata["models"] = models

    # 3. Save back to GCS
    upload_url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket_name}/o?uploadType=media&name={METADATA_FILE_NAME}"
    client.session.post(upload_url, data=json.dumps(metadata, indent=2), headers={"Content-Type": "application/json"})

def start_download(client: GCPClient, source: str, model_id: str, bucket_name: str, hf_token: Optional[str] = None, use_secret: bool = False) -> str:
    """Start Cloud Build job for downloading a model."""
    substitutions = {
        "_MODEL_ID": model_id,
        "_BUCKET_NAME": bucket_name,
    }
    
    available_secrets = None
    hf_token_env = "$_HF_TOKEN"
    
    if use_secret:
        # Import here to avoid circular dependency if any
        from cr_infer.secrets import HF_TOKEN_SECRET_NAME
        available_secrets = {
            "secretManager": [
                {
                    "versionName": f"projects/{client.project_id}/secrets/{HF_TOKEN_SECRET_NAME}/versions/latest",
                    "env": "HF_TOKEN"
                }
            ]
        }
        hf_token_env = "$$HF_TOKEN"
    elif hf_token:
        substitutions["_HF_TOKEN"] = hf_token

    # Step to update metadata to 'completed'
    update_metadata_script = """
import json, os, subprocess
metadata_file = 'llm-manager-metadata.json'
bucket = os.environ['_BUCKET_NAME']
model_id = os.environ['_MODEL_ID']

# Download current metadata
subprocess.run(['gsutil', 'cp', f'gs://{bucket}/{metadata_file}', metadata_file])

with open(metadata_file, 'r') as f:
    metadata = json.load(f)

for m in metadata.get('models', []):
    if m['id'] == model_id:
        m['status'] = 'completed'
        m['downloadedAt'] = subprocess.check_output(['date', '--iso-8601=seconds']).decode().strip()

# Upload updated metadata
with open(metadata_file, 'w') as f:
    json.dump(metadata, f, indent=2)
subprocess.run(['gsutil', 'cp', metadata_file, f'gs://{bucket}/{metadata_file}'])
"""

    common_final_steps = [
        {
            "name": "gcr.io/cloud-builders/gsutil",
            "entrypoint": "python3",
            "args": ["-c", update_metadata_script],
            "id": "update_metadata_success",
            "env": [
                "_BUCKET_NAME=$_BUCKET_NAME",
                "_MODEL_ID=$_MODEL_ID"
            ]
        }
    ]

    if source == "huggingface":
        step = {
            "name": "python:3.10-slim",
            "entrypoint": "bash",
            "args": [
                "-c",
                f"pip install huggingface_hub && hf download $_MODEL_ID --local-dir /workspace/model-repo --token {hf_token_env}"
            ],
            "id": "download_model_repo"
        }
        if use_secret:
            step["secretEnv"] = ["HF_TOKEN"]
            
        steps = [
            step,
            {
                "name": "gcr.io/cloud-builders/gsutil",
                "args": ["-m", "cp", "-r", "/workspace/model-repo", "gs://$_BUCKET_NAME/$_MODEL_ID"],
                "id": "Upload to GCS"
            },
            *common_final_steps
        ]
    elif source == "ollama":
        steps = [
            {
                "name": "ollama/ollama",
                "entrypoint": "bash",
                "env": ["HOME=/workspace"],
                "args": [
                    "-c",
                    "ollama serve & sleep 5 && ollama pull $_MODEL_ID"
                ],
                "id": "download_ollama_model"
            },
            {
                "name": "gcr.io/cloud-builders/gsutil",
                "args": ["-m", "cp", "-r", "/workspace/.ollama/*", "gs://$_BUCKET_NAME/ollama/"],
                "id": "upload_to_gcs"
            },
            *common_final_steps
        ]
    else:
        raise ValueError(f"Unsupported source: {source}")

    build_config = {
        "steps": steps,
        "timeout": "7200s",
        "options": {"machineType": "E2_HIGHCPU_8"},
        "substitutions": substitutions
    }
    
    if available_secrets:
        build_config["availableSecrets"] = available_secrets

    operation = client.trigger_build(build_config)
    build_id = operation.get("metadata", {}).get("build", {}).get("id") or operation.get("name", "").split("/")[-1]
    
    # Update GCS metadata to 'downloading'
    model_data = {
        "id": model_id,
        "source": source,
        "status": "downloading",
        "buildId": build_id,
        "submittedAt": "now" # Simple placeholder
    }
    update_metadata(client, bucket_name, model_data)
    
    return build_id
