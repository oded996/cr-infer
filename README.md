# cr-infer

`cr-infer` is a powerful CLI tool and library designed to simplify the deployment of AI workloads on **Google Cloud Run with GPUs**. It automates the complex steps of downloading models from Hugging Face or Ollama to Google Cloud Storage (GCS) and deploying them with optimized configurations.

## Features

- **Automated Model Downloads**: Download models directly from Ollama or Hugging Face to GCS using Cloud Build.
- **GPU Quota Management**: Easily check and request GPU quotas across all supported regions.
- **Smart Deployment**: Automatically configures Cloud Run V2 services with GPU accelerators, GCS volume mounts, and Direct VPC Egress.
- **Interactive Wizard**: Run any command without flags to enter a guided interactive mode.
- **Real-time Chat**: Test your deployed models immediately with a built-in streaming chat interface.
- **Metadata Synchronization**: Compatible with the [Cloud Run LLM Manager](https://github.com/oded996/cloud-run-llm-manager) UI.

## Prerequisites

- [Google Cloud CLI (gcloud)](https://cloud.google.com/sdk/docs/install) installed and configured.
- A Google Cloud Project with billing enabled.

## Authentication

Before using `cr-infer`, you must authenticate with Google Cloud and set your Application Default Credentials:

```bash
gcloud auth login
gcloud auth application-default login
```

## Installation

### From Source
Clone the repository and install it in editable mode:

```bash
git clone https://github.com/oded996/cr-infer.git
cd cr-infer
python3 -m pip install -e .
```

### Fallback (Manual execution)
If you prefer not to install the package, you can run it directly:
```bash
alias cr-infer='PYTHONPATH=$(pwd)/src python3 src/cr_infer/cli/main.py'
```

## Quick Start

1. **Verify your environment**:
   ```bash
   cr-infer check --project [PROJECT_ID]
   ```

2. **Check GPU Quotas**:
   ```bash
   cr-infer quota --project [PROJECT_ID]
   ```

3. **Download a Model** (Interactive):
   ```bash
   cr-infer model download
   ```

4. **Deploy a Model** (Interactive):
   ```bash
   cr-infer model deploy
   ```

5. **Chat with your Model**:
   ```bash
   cr-infer services chat [SERVICE_NAME] --region [REGION]
   ```

## Command Reference

`cr-infer` supports both interactive prompts (if flags are missing) and traditional command-line arguments.

### General Commands
- **`check`**: Verify environment readiness.
  - `--project, -p`: GCP Project ID.
- **`quota`**: Check GPU quota limits.
  - `--project, -p`: GCP Project ID.
  - `--region, -r`: Specific region to check.
  - `--gpu, -g`: Specific GPU type (e.g., `nvidia-l4`).

### Model Management (`model`)
- **`model download`**: Pull a model to GCS.
  - `--source, -s`: `huggingface` or `ollama`.
  - `--model-id, -m`: The model name/identifier.
  - `--bucket, -b`: Target GCS bucket.
  - `--token, -t`: Hugging Face API token (for gated models).
  - `--wait/--no-wait`: Whether to wait for completion and stream logs (default is `--wait`).
- **`model status [BUILD_ID]`**: Check the status of a download job.
- **`model logs [BUILD_ID]`**: View the Cloud Build logs for a download.
- **`model deploy`**: Deploy a model to Cloud Run.
  - `--name`: Service name.
  - `--model-id, -m`: Model ID in the bucket.
  - `--bucket, -b`: Source GCS bucket.
  - `--gpu, -g`: GPU type.
  - `--framework, -f`: `ollama`, `vllm`, or `zml`.
  - `--min-instances`: Minimum replicas (default: 0).
  - `--max-instances`: Maximum replicas (default: 1).
  - `--subnet`: VPC subnet for Direct VPC Egress.

### Listing Commands
- **`models list`**: List models in buckets.
  - `--bucket, -b`: (Optional) Limit scan to a specific bucket.
- **`gcs list-buckets`**: List all buckets in the project with their regions.
- **`services list`**: List managed Cloud Run services.
  - `--region, -r`: Region to scan.

### Service Operations (`services`)
- **`services info [NAME]`**: Get full service configuration JSON.
- **`services logs [NAME]`**: View or stream logs.
  - `--limit`: Number of recent lines to fetch.
  - `--follow, -f`: Enable real-time streaming.
- **`services chat [NAME]`**: Start an interactive chat session.
- **`services delete [NAME]`**: Remove a Cloud Run service.

## Regional Alignment
`cr-infer` enforces that your Cloud Run service is deployed in the **same region** as your model bucket to ensure low latency and compatibility with GCS volume mounting. It will automatically detect and use the bucket's region during deployment.
