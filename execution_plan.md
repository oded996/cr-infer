# Execution Plan: cr-infer CLI & Library

This document provides a detailed plan and user guide for `cr-infer`, a Python-based tool to simplify AI deployments on Google Cloud Run with GPUs.

## 1. Architectural Details

The tool is split into a core library and a CLI wrapper to ensure reusability (e.g., for an MCP server).

### Core Components:
- **`cr_infer.config`**: Static configuration for regions, GPUs, and framework defaults.
- **`cr_infer.client`**: High-level wrapper for GCP SDKs (Storage, Run, Cloud Build, Logging, Quotas).
- **`cr_infer.models`**: Logic for Hugging Face/Ollama preflight checks and download orchestration.
- **`cr_infer.deployer`**: Handles Cloud Run service creation/updates with GPU and GCS volume mounting.
- **`cr_infer.cli`**: Typer-based interface with interactive fallbacks.

## 2. Technical Specifications (from Reference Code)

### Supported Regions & GPUs:
| Region | GPU Type | VRAM | Status |
| :--- | :--- | :--- | :--- |
| `us-central1` | NVIDIA L4 (24GB), RTX 6000 (96GB) | 24/96GB | GA / Private Preview |
| `us-east4` | NVIDIA L4 (24GB), H100 (80GB) | 24/80GB | GA / Private Preview |
| `europe-west1`| NVIDIA L4 (24GB) | 24GB | GA |
| `europe-west4`| NVIDIA L4 (24GB), RTX 6000 (96GB) | 24/96GB | GA / Private Preview |
| `asia-southeast1`| NVIDIA L4 (24GB), RTX 6000 (96GB) | 24/96GB | GA / Private Preview |

### Framework Defaults:
- **Ollama**:
    - Image: `ollama/ollama`
    - Port: `11434`
    - Env: `OLLAMA_MODELS=/gcs/{bucket}/ollama/models`, `OLLAMA_NUM_PARALLEL={concurrency}`
- **vLLM**:
    - Image: `vllm/vllm-openai`
    - Port: `8000`
    - Args: `--model=/gcs/{bucket}/{model_id}`, `--load-format=runai_streamer`, `--gpu-memory-utilization=0.8`
- **ZML**:
    - Image: `zmlai/llmd`
    - Port: `8000`
    - Args: `--model-dir=/model/{model_id}`, `--backend=triton`

## 3. Implementation Phases

**Note:** The CLI will be updated at *every* phase so that new functionality can be tested immediately.

### Phase 1: Foundation, Auth & Initial CLI
- Implement `cr_infer` project structure and `config` module.
- Implement `check` command to verify `gcloud` auth.
- Verify project permissions and API status (Run, Build, Storage, Quotas).
- **Testable CLI:** `cr-infer check`

### Phase 2: Quota Management
- Implement `cr_infer.quota` module using the Cloud Quotas API.
- Add logic to fetch both Zonal and Non-Zonal limits for specified GPUs.
- **Testable CLI:** `cr-infer quota --region ... --gpu ...`

### Phase 3: Model Management
- **Preflight**: Implement `hf_preflight` and `ollama_preflight` (model existence/size).
- **Download**: Port Cloud Build orchestration logic (Hugging Face & Ollama).
- **Metadata**: Implement GCS metadata management (`llm-manager-metadata.json`).
- **Testable CLI:** `cr-infer model download`, `cr-infer models list`

### Phase 4: Deployment Engine
- **Validation**: Calculate VRAM needs and compare against selected GPU.
- **Service Configuration**:
    - Create/Update Cloud Run V2 Services.
    - Configure GPU accelerators, GCS volume mounts, and launch stages.
    - Implement optional VPC networking.
- **Testable CLI:** `cr-infer model deploy`

### Phase 5: Service Operations & Chat
- Implement `services list`, `info`, and `update` commands.
- Implement `logs` streaming from Cloud Logging.
- Build the `chat` interface with streaming proxy logic.
- **Testable CLI:** `cr-infer service info`, `cr-infer service update`, `cr-infer service logs`, `cr-infer service chat`

### Phase 6: Interactive Mode & UX Polishing
- Implement `questionary` fallbacks for all commands when flags are missing.
- Add progress indicators for long-running operations.
- Final documentation and distribution checks.

---

# User Guide: cr-infer CLI

## Installation
```bash
pip install cr-infer
```

## Commands

### `check`
Verify your environment and project permissions.
```bash
cr-infer check --project [PROJECT_ID]
```

### `quota`
Check GPU quota for a specific region.
```bash
cr-infer quota --project [PROJECT_ID] --region us-central1 --gpu nvidia-l4
```

### `model download`
Download a model from Hugging Face or Ollama to GCS.
```bash
cr-infer model download \
  --source huggingface \
  --model google/gemma-3-4b-it \
  --bucket my-models-bucket \
  --token [HF_TOKEN]
```

### `models list`
List models already downloaded to GCS.
```bash
cr-infer models list --bucket my-models-bucket
```

### `model deploy`
Deploy a model to Cloud Run + GPU.
```bash
cr-infer model deploy \
  --model google/gemma-3-4b-it \
  --bucket my-models-bucket \
  --gpu nvidia-l4 \
  --region us-central1 \
  --framework vllm
```

### `services list`
List all Cloud Run services managed by `cr-infer`.
```bash
cr-infer services list
```

### `service info`
Show detailed configuration for a service.
```bash
cr-infer service info [SERVICE_NAME]
```

### `service update`
Update service configuration (e.g., scaling, concurrency).
```bash
cr-infer service update [SERVICE_NAME] --min-instances 1 --max-instances 5
```

### `service logs`
Stream logs from the service.
```bash
cr-infer service logs [SERVICE_NAME] --follow
```

### `service chat`
Start an interactive chat session with the deployed model.
```bash
cr-infer service chat [SERVICE_NAME]
```