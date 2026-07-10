import json
import sys
import click
from typing import Optional, List, Tuple, Dict, Any, Union
from cr_infer.client import GCPClient

def prompt_if_missing(value: Optional[str], name: str, choices: Optional[List[Union[str, Dict[str, Any]]]] = None, message: str = None) -> str:
    if value:
        return value
    
    from InquirerPy import inquirer
    msg = message or f"Select {name}:"
    if choices:
        return inquirer.select(message=msg, choices=choices).execute()
    else:
        return inquirer.text(message=msg).execute()

def get_effective_project(project: Optional[str]) -> str:
    if project:
        return project
    
    # Try to get from gcloud config
    import subprocess
    try:
        res = subprocess.run(["gcloud", "config", "get-value", "project"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            p = res.stdout.strip()
            click.echo(f"Project ID not provided. Using default project: {click.style(p, fg='yellow')}")
            return p
    except:
        pass
    return None

def print_row(label: str, value: Any, W: int = 140, color: str = "white"):
    if isinstance(value, list):
        if not value:
            value_str = "None"
        else:
            value_str = "\n" + "\n".join([f"                       {v}" for v in value])
    else:
        value_str = str(value)
    
    label_str = click.style(f"  {label:<20}", bold=True)
    val_styled = click.style(value_str, fg=color)
    click.echo(f"║{label_str} {val_styled:<{W-23}}║")

def print_service_table(service_payload: Dict[str, Any], region: str, title: str = "Planned Deployment Configuration"):
    template = service_payload.get("template", {})
    container = template.get("containers", [{}])[0]
    resources = container.get("resources", {}).get("limits", {})
    scaling = template.get("scaling", {})

    # Mapping for readability
    name = service_payload.get("name", "").split("/")[-1]
    if not name: name = "[New Service]"

    W = 140
    click.echo("\n╔" + "═" * (W - 2) + "╗")
    title_styled = click.style(f"  {title:<{W-4}}", bold=True)
    click.echo(f"║{title_styled}║")
    click.echo("╠" + "═" * (W - 2) + "╣")

    print_row("Service Name:", name)
    print_row("Region:", region)
    
    if "updateTime" in service_payload:
        print_row("Last Updated:", service_payload["updateTime"])
    
    print_row("Container Image:", container.get("image", "Unknown"))
    print_row("GPU:", template.get("nodeSelector", {}).get("accelerator", "None"))
    print_row("GPU Count:", resources.get("nvidia.com/gpu", "1"))
    print_row("vCPUs:", resources.get("cpu", "Default"))
    print_row("Memory:", resources.get("memory", "Default"))
    
    launch_stage = service_payload.get("launchStage")
    if launch_stage and launch_stage != "GA":
        print_row("Launch Stage:", launch_stage, color="yellow")

    print_row("Min Instances:", scaling.get("minInstanceCount", 0))
    print_row("Max Instances:", scaling.get("maxInstanceCount", "Default"))
    
    zonal_red = "Disabled (Lower Cost)" if template.get("gpuZonalRedundancyDisabled") else "Enabled (HA)"
    print_row("Zonal Redundancy:", zonal_red)
    
    # Networking
    ann = template.get("annotations", {})
    nw_interfaces = ann.get("run.googleapis.com/network-interfaces")
    if nw_interfaces:
        try:
            if isinstance(nw_interfaces, str):
                nw_interfaces = json.loads(nw_interfaces)
            subnet = nw_interfaces[0].get("subnetwork", "Unknown")
            print_row("VPC Subnetwork:", subnet)
        except:
            print_row("VPC Subnetwork:", "Unknown")
    else:
        print_row("VPC Subnetwork:", "None")

    # Storage
    vols = template.get("volumes", [])
    mounts = []
    for v in vols:
        if "gcs" in v:
            mounts.append(f"{v['gcs']['bucket']} → /gcs/{v['gcs']['bucket']}")
    print_row("GCS Mounts:", mounts)

    # Env and Args
    env_list = [f"{e['name']}={e['value']}" for e in container.get("env", [])]
    print_row("Env Variables:", env_list)
    print_row("Arguments:", container.get("args", []))

    click.echo("╚" + "═" * (W - 2) + "╝")

    # Add console link outside the table for easy clicking
    full_name = service_payload.get("name", "")
    if full_name:
        parts = full_name.split("/")
        if len(parts) >= 6:
            project = parts[1]
            svc_region = parts[3]
            svc_name = parts[5]
            console_url = f"https://console.cloud.google.com/run/detail/{svc_region}/{svc_name}/metrics?project={project}"
            click.echo(f"{click.style('Cloud Run Console:', bold=True)} {console_url}\n")

@click.group()
def cli():
    """CLI tool to deploy AI workloads on Cloud Run with GPUs"""
    pass

def get_gpu_choices(region: str) -> List[Dict[str, Any]]:
    from cr_infer.config import get_region_config
    region_cfg = get_region_config(region)
    if not region_cfg:
        return []
    sorted_gpus = sorted(region_cfg.gpus, key=lambda x: x.vram_gb)
    return [{"name": f"{g.name} ({g.vram_gb}GB)", "value": g.name} for g in sorted_gpus]

@cli.command()
@click.option("--project", "-p", help="GCP Project ID")
def check(project):
    """Verify authentication, project permissions, and required APIs."""
    project = get_effective_project(project)

    try:
        client = GCPClient(project_id=project)
    except Exception as e:
        click.secho(f"Error initializing GCP Client: {e}", fg="red")
        sys.exit(1)

    if not client.project_id:
        click.secho("Error: Project ID not specified and could not be determined from environment.", fg="red")
        sys.exit(1)

    click.echo(f"Checking project: {click.style(client.project_id, bold=True, fg='cyan')}\n")

    # 1. Auth Check
    is_auth, auth_msg = client.verify_auth()
    if is_auth:
        click.echo(f"[{click.style('✔', fg='green')}] Authenticated as: {click.style(auth_msg, bold=True, fg='yellow')}")
    else:
        click.secho(f"[✘] Authentication: {auth_msg}", fg="red")
        sys.exit(1)

    # 2. API Checks
    def print_status(name, ok, msg=""):
        status = click.style("✔", fg="green") if ok else click.style("✘", fg="red")
        label = click.style(f"{name:<40}", fg="white")
        click.echo(f"[{status}] {label} {msg}")

    required_apis = [
        "run.googleapis.com",
        "storage.googleapis.com",
        "cloudbuild.googleapis.com",
        "logging.googleapis.com",
        "secretmanager.googleapis.com",
        "cloudresourcemanager.googleapis.com"
    ]
    
    for api in required_apis:
        enabled = client.check_api_enabled(api)
        print_status(api, enabled, "" if enabled else f"Disabled - Run: {click.style(f'gcloud services enable {api}', fg='cyan')}")

    optional_apis = [
        "artifactregistry.googleapis.com",
        "vpcaccess.googleapis.com"
    ]
    for api in optional_apis:
        enabled = client.check_api_enabled(api)
        if enabled:
            print_status(api, True, "Enabled (Optional)")
        else:
            enable_cmd = f"gcloud services enable {api}"
            print_status(api, False, f"Disabled (Optional) - Run: {click.style(enable_cmd, fg='cyan')}")

@cli.command()
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
@click.option("--gpu", "-g", help="GPU Type (e.g. nvidia-l4)")
def quota(project, region, gpu):
    """Check GPU quota for a specific region."""
    from cr_infer.quota import fetch_gpu_quota
    project = get_effective_project(project)
    client = GCPClient(project_id=project)

    from cr_infer.config import list_supported_regions, list_supported_gpus
    region = prompt_if_missing(region, "Region", choices=list_supported_regions())
    gpu = prompt_if_missing(gpu, "GPU Type", choices=get_gpu_choices(region))

    try:
        q = fetch_gpu_quota(client, region, gpu)
        click.echo(f"\nQuota for {click.style(gpu, bold=True)} in {click.style(region, bold=True)}:")
        click.echo(f"  Non-Zonal Limit: {click.style(str(q['non_zonal']), fg='green')}")
        click.echo(f"  Zonal Limit:     {click.style(str(q['zonal']), fg='green')}")
    except Exception as e:
        click.secho(f"Error fetching quota: {e}", fg="red")

@cli.group()
def model():
    """Model management commands (download, status)"""
    pass

@model.command(name="download")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
@click.option("--model", "-m", help="Hugging Face Model ID (e.g. google/gemma-2b)")
@click.option("--bucket", "-b", help="GCS Bucket name")
@click.option("--token", "-t", help="Hugging Face Token")
def model_download(project, region, model, bucket, token):
    """Download a model from Hugging Face to GCS."""
    from cr_infer.models import hf_preflight, start_download, list_models_in_bucket
    from cr_infer.config import list_supported_regions
    from cr_infer.secrets import ensure_hf_token

    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    
    region = prompt_if_missing(region, "Region", choices=list_supported_regions())
    model = prompt_if_missing(model, "HF Model ID")

    # Pre-flight check
    hf_token, use_secret = ensure_hf_token(client, provided_token=token)
    try:
        info = hf_preflight(model, token=hf_token)
    except Exception as e:
        click.secho(f"Error connecting to Hugging Face: {e}", fg="red")
        return

    size_gb = info['size'] / (1024**3)
    click.echo(f"\nModel: {click.style(model, bold=True)}")
    click.echo(f"Size:  {click.style(f'{size_gb:.2f} GB', fg='green')}")
    if info['gated']:
        click.echo(f"Gated: {click.style('Yes', fg='yellow')}")
    
    if not hf_token and info['gated']:
        click.secho("Error: This model is gated. Please provide a Hugging Face token with --token.", fg="red")
        return

    # Bucket selection
    buckets = client.list_buckets()
    bucket_choices = [b["name"] for b in buckets if b.get("location", "").lower() == region.lower()]
    
    if not bucket_choices:
        click.secho(f"No buckets found in region {region}. Please create one first.", fg="red")
        return
        
    bucket = prompt_if_missing(bucket, "GCS Bucket", choices=bucket_choices)

    # Check if model already exists
    existing = list_models_in_bucket(client, bucket)
    if any(m["id"] == model for m in existing):
        if not click.confirm(f"Model {model} already exists in {bucket}. Download again?"):
            return

    # Start download
    try:
        build_id = start_download(client, "huggingface", model, bucket, hf_token=hf_token, use_secret=use_secret, size=info.get('size', 0))
        click.secho(f"\n✔ Download task submitted! Build ID: {build_id}", fg="green")
        click.echo(f"Follow logs: {click.style(f'python3 {sys.argv[0]} model logs {build_id} --project {client.project_id}', fg='cyan')}")
    except Exception as e:
        click.secho(f"Error starting download: {e}", fg="red")

@model.command(name="status")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--id", "-i", "build_id", help="Cloud Build ID")
def model_status(project, build_id):
    """Check the status of a model download task."""
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    
    if not build_id:
        # List recent builds
        builds = client.list_builds(limit=5)
        if not builds:
            click.echo("No recent model download tasks found.")
            return
        
        choices = []
        for b in builds:
            m = b.get("substitutions", {}).get("_MODEL_ID", "Unknown")
            status = b.get("status", "Unknown")
            choices.append({"name": f"{m} ({status}) - {b['id'][:8]}", "value": b["id"]})
        
        build_id = inquirer.select(message="Select task to check:", choices=choices).execute()

    try:
        status = client.get_build_status(build_id)
        click.echo(f"Task Status: {click.style(status, bold=True, fg='yellow')}")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@model.command(name="logs")
@click.option("--project", "-p", help="GCP Project ID")
@click.argument("build_id")
@click.option("--follow", "-f", is_flag=True, help="Follow logs")
def model_logs(project, build_id, follow):
    """View logs for a model download task."""
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    
    try:
        client.stream_build_logs(build_id, follow=follow)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@click.group()
def models():
    """List models in GCS buckets."""
    pass

@models.command(name="list")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--bucket", "-b", help="GCS Bucket name")
def models_list(project, bucket):
    """List models stored in GCS buckets."""
    from cr_infer.models import list_models_in_bucket
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    
    buckets_to_check = [bucket] if bucket else [b["name"] for b in client.list_buckets()]
    
    if not buckets_to_check:
        click.echo("No buckets found.")
        return

    found_any = False
    for b_name in buckets_to_check:
        models = list_models_in_bucket(client, b_name)
        if models:
            found_any = True
            click.echo(f"\nBucket: {click.style(b_name, bold=True, fg='cyan')}")
            for m in models:
                size_bytes = m.get("size") or m.get("total_size") or 0
                if not size_bytes and m.get("status") == "completed":
                    size_bytes = client.get_gcs_prefix_size(b_name, m['id'])
                size_gb = size_bytes / (1024**3)
                status = m.get("status", "completed")
                status_color = "green" if status == "completed" else "yellow"
                click.echo(f"  - {m['id']:<40} {size_gb:>6.2f} GB  [{click.style(status, fg=status_color)}]")
    
    if not found_any:
        click.echo("No models found in any buckets.")

@click.group()
def gcs():
    """GCS bucket management."""
    pass

@gcs.command(name="list-buckets")
@click.option("--project", "-p", help="GCP Project ID")
def gcs_list_buckets(project):
    """List all GCS buckets in the project."""
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    
    buckets = client.list_buckets()
    if not buckets:
        click.echo("No buckets found.")
        return
        
    click.echo(f"\nBuckets in {click.style(client.project_id, bold=True)}:")
    for b in buckets:
        click.echo(f"  - {b['name']:<30} [{b.get('location', 'Unknown')}]")

@model.command(name="deploy")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
@click.option("--bucket", "-b", help="GCS Bucket name")
@click.option("--model", "-m", "model_id", help="Model ID")
@click.option("--framework", "-f", help="Framework (vllm, ollama, zml)")
@click.option("--gpu", "-g", help="GPU Type (e.g. nvidia-l4)")
@click.option("--cpu", help="vCPUs")
@click.option("--memory", help="Memory (e.g. 16Gi)")
@click.option("--name", help="Service name")
@click.option("--min-instances", default=0, help="Min instances")
@click.option("--max-instances", default=1, help="Max instances")
@click.option("--subnet", help="VPC Subnetwork name")
@click.option("--dflash-model", help="DFlash drafter model for ZML speculative decoding")
def model_deploy(project, region, bucket, model_id, framework, gpu, cpu, memory, name, min_instances, max_instances, subnet, dflash_model):
    """Deploy a model to Cloud Run with GPU support."""
    from cr_infer.deployer import CloudRunDeployer
    from cr_infer.models import list_models_in_bucket
    from cr_infer.config import list_supported_regions, list_supported_gpus
    
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)

    if not bucket:
        click.echo("No bucket or model specified. Scanning all buckets for available models...")
        all_buckets = client.list_buckets()
        model_choices = []
        for b in all_buckets:
            models = list_models_in_bucket(client, b["name"])
            for m in models:
                model_choices.append({
                    "name": f"{m['id']} (gs://{b['name']} in {b['location']})",
                    "value": (b["name"], m["id"], b["location"])
                })
        
        if not model_choices:
            click.secho("No models found in any buckets. Download one first.", fg="red")
            return
            
        bucket, model_id, bucket_region = prompt_if_missing(None, "Model", choices=model_choices, message="Select model to deploy:")
    else:
        # Verify bucket exists and get its region
        all_buckets = client.list_buckets()
        bucket_info = next((b for b in all_buckets if b["name"] == bucket), None)
        if not bucket_info:
            click.secho(f"Error: Bucket {bucket} not found.", fg="red")
            return
        bucket_region = bucket_info.get("location", "").lower()

    if region and region.lower() != bucket_region.lower():
        click.secho(f"Warning: Overriding region {region} with bucket region {bucket_region}.", fg="yellow")
    region = bucket_region

    click.echo(f"Using region: {click.style(region, fg='cyan')} (from bucket)")

    if not model_id:
        models = list_models_in_bucket(client, bucket)
        if not models:
            click.echo(f"No models found in bucket {bucket}. Download one first.")
            return
        model_choices = [m["id"] for m in models]
        model_id = prompt_if_missing(model_id, "Model", choices=model_choices, message="Select model to deploy:")

    # Get model source to determine framework options
    models = list_models_in_bucket(client, bucket)
    model_data = next((m for m in models if m["id"] == model_id), None)
    
    if model_data:
        source = model_data.get("source")
        if source == "ollama":
            click.echo(f"Model source is Ollama. Forcing framework to {click.style('ollama', fg='green')}.")
            framework = "ollama"
        elif source == "huggingface":
            click.echo(f"Model source is Hugging Face.")
            framework = prompt_if_missing(framework, "Framework", choices=["vllm", "zml"])
    else:
        # Fallback if metadata is missing
        framework = prompt_if_missing(framework, "Framework", choices=["ollama", "vllm", "zml"])

    gpu = prompt_if_missing(gpu, "GPU Type", choices=get_gpu_choices(region))
    
    # Get GPU config to determine minimum CPU/Memory if not provided via flags
    from cr_infer.config import get_gpu_config
    gpu_cfg = get_gpu_config(region, gpu)
    
    effective_cpu = cpu
    effective_memory = memory
    
    if not effective_cpu or not effective_memory:
        if gpu_cfg:
            effective_cpu = effective_cpu or gpu_cfg.validCpus[0]
            effective_memory = effective_memory or gpu_cfg.validMemory[0]
        else:
            effective_cpu = effective_cpu or "8"
            effective_memory = effective_memory or "16Gi"

    framework = prompt_if_missing(framework, "Framework", choices=["ollama", "vllm", "zml"])
    
    if not name:
        default_name = f"{framework}-{model_id.replace(':', '-').replace('/', '-')}"[:63].lower()
        name = click.prompt("Enter service name", default=default_name)

    # VPC setup
    network = None
    if not subnet:
        if click.confirm("Do you want to use Direct VPC Egress for faster model loading?", default=True):
            subnets = client.list_subnets(region)
            if not subnets:
                click.secho(f"No subnets found in {region}. Skipping VPC.", fg="yellow")
            else:
                # Put 'default' subnet at the top if it exists
                subnet_choices = sorted([s["name"] for s in subnets], key=lambda x: x != "default")
                default_sub_name = "default" if "default" in subnet_choices else subnet_choices[0]
                
                subnet = prompt_if_missing(None, "Subnet", choices=subnet_choices, message=f"Select subnet (default: {default_sub_name}):") or default_sub_name
                
                # Check PGA
                selected_sub_obj = next(s for s in subnets if s["name"] == subnet)
                network = selected_sub_obj.get("network", "").split("/")[-1]
                if not selected_sub_obj.get("privateIpGoogleAccess"):
                    click.secho(f"Warning: Private Google Access is disabled for subnet {subnet}.", fg="yellow")
                    if click.confirm("Enable Private Google Access? (Required for GCS mounting over VPC)"):
                        client.patch_subnet(region, subnet, {"privateIpGoogleAccess": True})
                        click.secho("✔ Private Google Access enabled.", fg="green")
    else:
        # If subnet was provided via flag, we try to find its network
        subnets = client.list_subnets(region)
        selected_sub_obj = next((s for s in subnets if s["name"] == subnet), None)
        if selected_sub_obj:
            network = selected_sub_obj.get("network", "").split("/")[-1]

    try:
        # 1. Validate model exists in bucket
        click.echo(f"Validating model {model_id} in bucket {bucket}...")
        models = list_models_in_bucket(client, bucket)
        model_data = next((m for m in models if m["id"] == model_id), None)
        if not model_data:
            click.secho(f"Warning: Model {model_id} not found in bucket metadata. Proceeding anyway.", fg="yellow")
            model_size = 0
        else:
            model_size = model_data.get("size") or model_data.get("total_size") or 0
            if not model_size and model_data.get("status") == "completed":
                model_size = client.get_gcs_prefix_size(bucket, model_id)

        # 2. VRAM Validation
        if model_size > 0:
            ok, msg = deployer.validate_vram(region, gpu, model_size)
            if not ok:
                click.secho(f"Warning: {msg}", fg="yellow")
                if not click.confirm("Do you want to continue?"):
                    return

        # 3. Default Images
        images = {
            "ollama": "ollama/ollama",
            "vllm": "vllm/vllm-openai",
            "zml": "zmlai/llmd"
        }
        image = images[framework]

        # 1. Build Payload
        payload = deployer.build_payload(
            name=name,
            region=region,
            image=image,
            model_id=model_id,
            bucket_name=bucket,
            gpu_type=gpu,
            framework=framework,
            cpu=effective_cpu,
            memory=effective_memory,
            min_instances=min_instances,
            max_instances=max_instances,
            subnet=subnet,
            network=network,
            dflash_model=dflash_model
        )

        # 2. Show Summary Table
        print_service_table(payload, region)

        # 3. Quota check
        from cr_infer.quota import fetch_gpu_quota
        try:
            q = fetch_gpu_quota(client, region, gpu)
            click.echo(f"  Note: Current Available Quota: {int(q['non_zonal'])} (Without Zonal Redundancy)")
        except:
            pass

        if not click.confirm("\nProceed with deployment?", default=True):
            return

        # 4. Deploy
        click.echo(f"Deploying service {click.style(name, bold=True)} to {region}...")
        deployer.deploy_service(name, region, payload)
        
        click.secho(f"✔ Deployment initiated!", fg="green")
        
        # 2. Tailored CLI Commands
        click.echo(f"\n{click.style('Useful Commands:', bold=True)}")
        click.echo(f"  Check Info:  python3 {sys.argv[0]} services info {name} --region {region} --project {client.project_id}")
        click.echo(f"  View Logs:   python3 {sys.argv[0]} services logs {name} --region {region} --project {client.project_id} --follow")
        click.echo(f"  Start Chat:  python3 {sys.argv[0]} services chat {name} --region {region} --project {client.project_id}")
        
        click.echo(f"\nNote: It may take a few minutes for the GPU instance to start and the model to load.")

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@click.group()
def services():
    """Cloud Run service management."""
    pass

@services.command(name="list")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
def services_list(project, region):
    """List Cloud Run services managed by cr-infer."""
    from cr_infer.config import list_supported_regions
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    
    regions = [region] if region else list_supported_regions()
    
    found_any = False
    for r in regions:
        services = deployer.list_services(r)
        if services:
            found_any = True
            click.echo(f"\nRegion: {click.style(r, bold=True, fg='cyan')}")
            for s in services:
                name = s["name"].split("/")[-1]
                image = s.get("template", {}).get("containers", [{}])[0].get("image", "Unknown")
                click.echo(f"  - {click.style(name, bold=True):<30} {image}")
    
    if not found_any:
        click.echo("No services found.")

@services.command(name="info")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
def services_info(name, project, region):
    """Get detailed information about a service."""
    from cr_infer.config import list_supported_regions
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    
    if not region:
        # Search all regions
        for r in list_supported_regions():
            svc = deployer.get_service(r, name)
            if svc:
                region = r
                break
    
    if not region:
        click.secho(f"Error: Service {name} not found in any supported region.", fg="red")
        return
        
    svc = deployer.get_service(region, name)
    if not svc:
        click.secho(f"Error: Service {name} not found in {region}.", fg="red")
        return

    print_service_table(svc, region, title=f"Service Details: {name}")

@services.command(name="logs")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
@click.option("--limit", default=50, help="Number of log entries")
@click.option("--follow", "-f", is_flag=True, help="Follow logs")
def services_logs(name, project, region, limit, follow):
    """View logs for a Cloud Run service."""
    from cr_infer.config import list_supported_regions
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    
    if not region:
        for r in list_supported_regions():
            if deployer.get_service(r, name):
                region = r
                break
    
    if not region:
        click.secho(f"Error: Service {name} not found.", fg="red")
        return
        
    try:
        if follow:
            client.stream_service_logs(region, name)
        else:
            logs = client.get_service_logs(region, name, limit=limit)
            for entry in logs:
                timestamp = entry.get("timestamp", "")
                severity = entry.get("severity", "DEFAULT")
                msg = entry.get("textPayload") or entry.get("jsonPayload", entry.get("protoPayload", "{}"))
                
                color = "white"
                if severity == "ERROR": color = "red"
                elif severity == "WARNING": color = "yellow"
                elif severity == "INFO": color = "green"
                
                click.echo(f"[{timestamp}] [{click.style(severity, fg=color)}] {msg}")
    except Exception as e:
        click.secho(f"Error fetching logs: {e}", fg="red")

@services.command(name="delete")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
def services_delete(name, project, region):
    """Delete a Cloud Run service."""
    from cr_infer.config import list_supported_regions
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    
    if not region:
        for r in list_supported_regions():
            if deployer.get_service(r, name):
                region = r
                break
    
    if not region:
        click.secho(f"Error: Service {name} not found.", fg="red")
        return
        
    if click.confirm(f"Are you sure you want to delete service {name} in {region}?"):
        try:
            deployer.delete_service(region, name)
            click.secho(f"✔ Service {name} deleted.", fg="green")
        except Exception as e:
            click.secho(f"Error: {e}", fg="red")

@services.command(name="chat")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
def services_chat(name, project, region):
    """Start an interactive chat session with a deployed model."""
    from cr_infer.config import list_supported_regions
    from cr_infer.deployer import CloudRunDeployer
    project = get_effective_project(project)
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    
    if not region:
        for r in list_supported_regions():
            if deployer.get_service(r, name):
                region = r
                break
    
    if not region:
        click.secho(f"Error: Service {name} not found.", fg="red")
        return
        
    svc = deployer.get_service(region, name)
    url = svc.get("uri") or f"https://{name}-qd4pckfcha-uc.a.run.app" # Fallback if not ready
    
    click.echo(f"\nStarting chat session with {click.style(name, bold=True)}...")
    click.echo(f"Service URL: {click.style(url, fg='cyan')}")
    click.echo("Type 'exit' or 'quit' to end the session.\n")

    history = [
        {"role": "system", "content": "You are a helpful AI assistant. Answer concisely."}
    ]
    
    import requests
    
    # Get auth token
    import subprocess
    token = subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True, text=True).stdout.strip()

    # Detect served model name from /v1/models
    served_model_name = name
    try:
        models_res = requests.get(f"{url}/v1/models", headers={"Authorization": f"Bearer {token}"}, timeout=5)
        if models_res.status_code == 200:
            models_data = models_res.json().get("data", [])
            if models_data and "id" in models_data[0]:
                served_model_name = models_data[0]["id"]
    except Exception:
        pass

    while True:
        user_input = click.prompt(click.style("User", fg="green", bold=True))
        if user_input.lower() in ["exit", "quit"]:
            break
            
        history.append({"role": "user", "content": user_input})
        
        try:
            # Use /v1/chat/completions (OpenAI compatible)
            payload = {
                "model": served_model_name,
                "messages": history,
                "stream": True,
                "temperature": 0.7,
                "max_tokens": 1024,
                "top_p": 0.9,
                "presence_penalty": 0.5,
                "frequency_penalty": 0.5,
                "stop": ["user:", "system:", "assistant:"]
            }
            
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(f"{url}/v1/chat/completions", json=payload, headers=headers, stream=True)
            
            if response.status_code != 200:
                click.secho(f"\nError: API returned {response.status_code}: {response.text}", fg="red")
                continue

            click.echo(click.style("Assistant: ", fg="yellow", bold=True), nl=False)
            full_response = ""
            
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8')
                    if line_str.startswith("data: "):
                        data_content = line_str[6:]
                        if data_content == "[DONE]":
                            break
                        try:
                            data_json = json.loads(data_content)
                            delta = data_json['choices'][0]['delta'].get('content', '')
                            full_response += delta
                            click.echo(delta, nl=False)
                        except:
                            pass
            
            click.echo("\n")
            history.append({"role": "assistant", "content": full_response})
            
        except Exception as e:
            click.secho(f"\nError connecting to service: {e}", fg="red")

cli.add_command(model)
cli.add_command(models)
cli.add_command(gcs)
cli.add_command(services)

if __name__ == "__main__":
    cli()
