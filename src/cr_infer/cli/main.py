import json
import sys
import click
from typing import Optional, List, Tuple
from cr_infer.client import GCPClient

def prompt_if_missing(value: Optional[str], name: str, choices: Optional[List[str]] = None, message: str = None) -> str:
    if value:
        return value
    
    from InquirerPy import inquirer
    msg = message or f"Select {name}:"
    if choices:
        return inquirer.select(message=msg, choices=choices).execute()
    else:
        return inquirer.text(message=msg).execute()

def print_header(text: str):
    click.echo(f"\n=== {text} ===")

def print_status(label: str, success: bool, message: str = ""):
    status = "✔" if success else "✘"
    color = "green" if success else "red"
    msg = f" {message}" if message else ""
    click.secho(f"[{status}] {label}{msg}", fg=color)

def format_bytes(size):
    if not size: return "Unknown"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

@click.group()
def cli():
    """CLI tool to deploy AI workloads on Cloud Run with GPUs"""
    pass

@cli.command()
@click.option("--project", "-p", help="GCP Project ID")
def check(project):
    """Verify authentication, project permissions, and required APIs."""
    if not project:
        from google.auth import default
        _, default_project = default()
        project = prompt_if_missing(project, "Project ID", message=f"Enter Project ID (default: {default_project}):") or default_project

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

    # 2. Permissions Check
    click.echo("\n--- IAM Permissions ---")
    required_permissions = [
        "run.services.get",
        "run.services.create",
        "storage.buckets.list",
        "cloudbuild.builds.create",
    ]
    
    results = client.check_permissions(required_permissions)
    for perm, granted in results:
        print_status(perm, granted)

    # 3. API Check
    click.echo("\n--- Required APIs ---")
    required_apis = [
        "run.googleapis.com",
        "storage.googleapis.com",
        "cloudbuild.googleapis.com",
        "logging.googleapis.com"
    ]
    
    for api in required_apis:
        enabled = client.check_api_enabled(api)
        if enabled:
            print_status(api, True, "Enabled")
        else:
            enable_url = f"https://console.cloud.google.com/apis/library/{api}?project={client.project_id}"
            print_status(api, False, f"Disabled - Enable at: {click.style(enable_url, fg='cyan')}")

@cli.command()
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
@click.option("--gpu", "-g", help="GPU Type (e.g. nvidia-l4)")
def quota(project, region, gpu):
    """Check GPU quota for a specific region."""
    from cr_infer.quota import fetch_gpu_quota
    from cr_infer.config import list_supported_regions, list_supported_gpus

    if not project:
        from google.auth import default
        _, default_project = default()
        project = prompt_if_missing(project, "Project ID", message=f"Enter Project ID (default: {default_project}):") or default_project

    client = GCPClient(project_id=project)
    
    regions_to_check = [region] if region else list_supported_regions()
    
    for r in regions_to_check:
        click.echo(f"\n{click.style('Region:', bold=True)} {click.style(r, fg='cyan')}")
        gpus_to_check = [gpu] if gpu else list_supported_gpus(r)
        
        if not gpus_to_check:
            click.echo("  No supported GPUs configured for this region.")
            continue

        # Table Header
        header = f"  {'GPU Type'.ljust(20)} {'Without Zonal Redundancy'.ljust(25)} {'With Zonal Redundancy'}"
        click.echo(click.style(header, underline=True, fg="white"))

        for g in gpus_to_check:
            try:
                quotas = fetch_gpu_quota(client, r, g)
                
                def fmt(val):
                    return str(int(val)) if val == int(val) else str(val)

                non_zonal = fmt(quotas['non_zonal'])
                zonal = fmt(quotas['zonal'])
                
                color = "green" if quotas['non_zonal'] > 0 or quotas['zonal'] > 0 else "yellow"
                row = f"  {g.ljust(20)} {non_zonal.ljust(25)} {zonal}"
                click.echo(click.style(row, fg=color))
            except Exception as e:
                click.echo(f"  {g.ljust(20)} {click.style(f'Error: {e}', fg='red')}")

    click.echo(f"\n{click.style('How to request more quota:', bold=True)} http://g.co/cloudrun/gpu-quota")
    click.echo("\n" + click.style("Note on Cloud Run GPU Redundancy Options:", bold=True))
    click.echo(f"  {click.style('1. With Zonal Redundancy (default):', fg='cyan')} Cloud Run reserves GPU capacity across multiple zones.")
    click.echo("     This offers higher reliability during zonal failures with an additional cost per GPU second.")
    click.echo(f"  {click.style('2. Without Zonal Redundancy:', fg='cyan')} Cloud Run attempts failover on a best-effort basis.")
    click.echo("     No guarantee of reserved capacity for failover, but results in a lower cost per GPU second.")
    click.echo("\n" + click.style("Tip:", fg="green") + " If you have 0 quota, Cloud Run will attempt to automatically acquire a small")
    click.echo("'Without Zonal Redundancy' quota for you during your first deployment.")

@cli.group()
def model():
    """Model management commands (download, status)"""
    pass

@model.command(name="download")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--source", "-s", type=click.Choice(["huggingface", "ollama"]), help="Model source")
@click.option("--model-id", "-m", help="Model ID")
@click.option("--bucket", "-b", help="Target GCS Bucket")
@click.option("--token", "-t", help="HF Token (optional)")
@click.option("--wait/--no-wait", default=False, help="Wait for download to complete and stream logs")
def model_download(project, source, model_id, bucket, token, wait):
    """Download a model to GCS using Cloud Build."""
    from cr_infer.models import start_download, hf_preflight, ollama_preflight
    
    if not project:
        from google.auth import default
        _, default_project = default()
        project = prompt_if_missing(project, "Project ID", message=f"Enter Project ID (default: {default_project}):") or default_project

    client = GCPClient(project_id=project)

    source = prompt_if_missing(source, "Source", choices=["huggingface", "ollama"])
    
    example = "google/gemma-3-4b-it" if source == "huggingface" else "gemma3:4b"
    model_id = prompt_if_missing(model_id, "Model ID", message=f"Enter Model ID (e.g. {example}):")

    if source == "huggingface" and not token:
        if click.confirm("Is this a gated model (requires token)?", default=False):
            token = prompt_if_missing(token, "HF Token")

    try:
        click.echo(f"Performing preflight check for {model_id}...")
        if source == "huggingface":
            info = hf_preflight(model_id, token)
        else:
            info = ollama_preflight(model_id)
        
        # Determine bucket and its region
        if not bucket:
            from cr_infer.config import list_supported_regions
            gpu_regions = list_supported_regions()
            all_buckets = client.list_buckets()
            valid_buckets = [b for b in all_buckets if b["location"] in gpu_regions]
            choices = [f"{b['name']} ({b['location']})" for b in valid_buckets] + ["+ Create New Bucket"]
            bucket_choice = prompt_if_missing(None, "Bucket", choices=choices, message="Select target bucket (GPU regions only):")
            
            if bucket_choice == "+ Create New Bucket":
                new_name = click.prompt("Enter new bucket name")
                new_location = prompt_if_missing(None, "Location", choices=gpu_regions, message="Select bucket location:")
                click.echo(f"Creating bucket {new_name} in {new_location}...")
                client.create_bucket(new_name, new_location)
                bucket = new_name
                bucket_region = new_location
            else:
                bucket = bucket_choice.split(" (")[0]
                bucket_region = bucket_choice.split(" (")[1].replace(")", "")
        else:
            all_buckets = client.list_buckets()
            bucket_info = next((b for b in all_buckets if b["name"] == bucket), None)
            if not bucket_info:
                click.secho(f"Error: Bucket {bucket} not found.", fg="red")
                return
            bucket_region = bucket_info["location"]
            
        # If bucket is multi-regional (us, eu, asia), we need to ask for a target region to show compatible GPUs
        from cr_infer.config import list_supported_regions
        gpu_regions = list_supported_regions()
        
        if bucket_region not in gpu_regions:
            click.secho(f"\n[!] Bucket is in multi-region '{bucket_region}'.", fg="yellow")
            bucket_region = prompt_if_missing(None, "Region", choices=gpu_regions, message="Select a target region to check GPU compatibility:")

        # --- Model Info Box ---
        size_bytes = info.get("total_size", 0)
        est_vram = size_bytes * 1.2 / (1024**3)
        
        width = 50 # Inner width of the box
        click.echo("\n" + "╔" + "═" * width + "╗")
        
        # Title: Style the padded string
        title_text = "Model Info Summary".ljust(width)
        click.echo(f"║ {click.style(title_text, bold=True)} ║")
        
        click.echo("╠" + "═" * width + "╣")
        
        def print_line(label, value):
            # label (12) + space (1) + value (37) = 50
            l_str = label.ljust(12)
            v_str = value.ljust(width - 13)
            click.echo(f"║ {click.style(l_str, fg='cyan')} {v_str} ║")

        print_line("Model:", model_id)
        print_line("Total Size:", format_bytes(size_bytes))
        print_line("Est. vRAM:", f"~{est_vram:.2f} GB")
        print_line("Region:", bucket_region)
        
        click.echo("║" + " " * width + "║")
        comp_title = "Compatible GPUs:".ljust(width)
        click.echo(f"║ {click.style(comp_title, bold=True)} ║")
        
        from cr_infer.config import get_region_config
        region_cfg = get_region_config(bucket_region)
        if region_cfg:
            for g in region_cfg.gpus:
                status = "[v]" if g.vram_gb >= est_vram or size_bytes == 0 else "[x]"
                color = "green" if status == "[v]" else "red"
                gpu_text = f"  {status} {g.name} ({g.vram_gb} GB)".ljust(width)
                click.echo(f"║ {click.style(gpu_text, fg=color)} ║")
        else:
             err_text = "  No GPU info for this region".ljust(width)
             click.echo(f"║ {click.style(err_text, fg='yellow')} ║")
        
        click.echo("╚" + "═" * width + "╝\n")

        if not click.confirm("Start download?", default=True):
            return

        click.echo(f"Submitting Cloud Build download job...")
        build_id = start_download(client, source, model_id, bucket, token)
        
        console_url = f"https://console.cloud.google.com/cloud-build/builds/{build_id}?project={client.project_id}"
        click.secho(f"✔ Build started: {build_id}", fg="green", bold=True)
        click.echo(f"{click.style('Console Link:', bold=True)} {console_url}\n")
        
        if wait:
            click.echo("Waiting for build and streaming logs (Ctrl+C to stop waiting)...")
            # Reuse logic from model logs
            import time
            last_logs = ""
            while True:
                status = client.get_build_status(build_id)
                state = status.get("status")
                
                # Fetch logs from GCS if available
                log_url = status.get("logsBucket")
                if log_url:
                    bucket_name = log_url.replace("gs://", "").split("/")[0]
                    log_object = f"log-{build_id}.txt"
                    try:
                        logs = client.get_build_logs(bucket_name, log_object)
                        if logs != last_logs:
                            new_content = logs[len(last_logs):]
                            click.echo(new_content, nl=False)
                            last_logs = logs
                    except Exception:
                        pass # Logs might not be ready yet

                if state not in ["WORKING", "QUEUED"]:
                    click.echo(f"\nBuild finished with status: {click.style(state, bold=True)}")
                    break
                time.sleep(5)
        else:
            click.echo(f"Track progress with: {click.style(f'cr-infer model status {build_id}', fg='cyan')}")
            
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@model.command(name="status")
@click.argument("build_id")
@click.option("--project", "-p", help="GCP Project ID")
def model_status(build_id, project):
    """Check the status of a model download build."""
    client = GCPClient(project_id=project)
    try:
        status = client.get_build_status(build_id)
        state = status.get("status")
        click.echo(f"Build ID: {build_id}")
        click.echo(f"Status:   ", nl=False)
        color = "green" if state == "SUCCESS" else "yellow" if state in ["WORKING", "QUEUED"] else "red"
        click.secho(state, fg=color)
        if status.get("logUrl"):
            click.echo(f"Logs:     {status.get('logUrl')}")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@model.command(name="logs")
@click.argument("build_id")
@click.option("--project", "-p", help="GCP Project ID")
def model_build_logs(build_id, project):
    """Fetch logs for a model download build."""
    client = GCPClient(project_id=project)
    try:
        status = client.get_build_status(build_id)
        log_url = status.get("logsBucket")
        if not log_url:
            click.echo("Logs bucket not found in build status.")
            return
        
        # log_url is usually gs://bucket/log-name.txt
        bucket = log_url.replace("gs://", "").split("/")[0]
        log_object = f"log-{build_id}.txt"
        
        click.echo(f"Fetching logs from gs://{bucket}/{log_object}...")
        logs = client.get_build_logs(bucket, log_object)
        click.echo("-" * 40)
        click.echo(logs)
        click.echo("-" * 40)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@cli.group()
def models():
    """List and manage downloaded models"""
    pass

@models.command(name="list")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--bucket", "-b", help="Bucket to list models from")
def models_list(project, bucket):
    """List models tracked in GCS buckets."""
    from cr_infer.models import list_models_in_bucket
    client = GCPClient(project_id=project)
    try:
        if bucket:
            buckets_to_check = [bucket]
        else:
            click.echo("No bucket specified. Scanning all buckets in project...")
            bucket_objects = client.list_buckets()
            buckets_to_check = [b["name"] for b in bucket_objects]

        found_any = False
        for b_name in buckets_to_check:
            models = list_models_in_bucket(client, b_name)
            if models:
                found_any = True
                click.echo(f"\nModels in gs://{b_name}:")
                for m in models:
                    click.echo(f"- {click.style(m['id'], fg='cyan')} ({m['source']}) [{m['status']}]")
        
        if not found_any:
            if bucket:
                click.echo(f"No models found in bucket '{bucket}' metadata.")
            else:
                click.echo("No managed models found in any bucket.")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@cli.group()
def gcs():
    """GCS bucket management"""
    pass

@gcs.command(name="list-buckets")
@click.option("--project", "-p", help="GCP Project ID")
def list_buckets_cmd(project):
    """List all GCS buckets in the project."""
    client = GCPClient(project_id=project)
    try:
        buckets = client.list_buckets()
        click.echo("Available GCS buckets:")
        for b in buckets:
            click.echo(f"  gs://{b['name']} ({b['location']})")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@model.command(name="deploy")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--name", help="Service name")
@click.option("--model-id", "-m", help="Model ID")
@click.option("--bucket", "-b", help="Source GCS Bucket")
@click.option("--region", "-r", help="GCP Region")
@click.option("--gpu", "-g", help="GPU Type")
@click.option("--framework", "-f", type=click.Choice(["ollama", "vllm", "zml"]), help="Serving framework")
@click.option("--min-instances", type=int, default=0)
@click.option("--max-instances", type=int, default=1)
@click.option("--subnet", help="VPC Subnet")
def model_deploy(project, name, model_id, bucket, region, gpu, framework, min_instances, max_instances, subnet):
    """Deploy a model to Cloud Run + GPU."""
    from cr_infer.deployer import CloudRunDeployer
    from cr_infer.models import list_models_in_bucket
    from cr_infer.config import list_supported_regions, list_supported_gpus

    if not project:
        from google.auth import default
        _, default_project = default()
        project = prompt_if_missing(project, "Project ID", message=f"Enter Project ID (default: {default_project}):") or default_project

    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)

    if not bucket:
        from cr_infer.config import list_supported_regions
        gpu_regions = list_supported_regions()
        all_buckets = client.list_buckets()
        valid_buckets = [b for b in all_buckets if b["location"] in gpu_regions]
        
        choices = [f"{b['name']} ({b['location']})" for b in valid_buckets]
        bucket_choice = prompt_if_missing(bucket, "Bucket", choices=choices, message="Select source bucket:")
        bucket = bucket_choice.split(" (")[0]

    # Find bucket region
    all_buckets = client.list_buckets()
    bucket_info = next((b for b in all_buckets if b["name"] == bucket), None)
    if not bucket_info:
        click.secho(f"Error: Bucket {bucket} not found.", fg="red")
        return
    
    bucket_region = bucket_info["location"]
    if region and region != bucket_region:
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

    gpu = prompt_if_missing(gpu, "GPU Type", choices=list_supported_gpus(region))
    
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
                default_sub = next((s for s in subnets if s["name"] == "default"), subnets[0])
                subnet_choices = [s["name"] for s in subnets]
                subnet = prompt_if_missing(None, "Subnet", choices=subnet_choices, message=f"Select subnet (default: {default_sub['name']}):") or default_sub["name"]
                
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
            model_size = model_data.get("size", 0)

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

        click.echo(f"Deploying service {name} to {region}...")
        op = deployer.deploy_service(
            name=name,
            region=region,
            image=image,
            model_id=model_id,
            bucket_name=bucket,
            gpu_type=gpu,
            framework=framework,
            min_instances=min_instances,
            max_instances=max_instances,
            subnet=subnet,
            network=network
        )
        click.secho(f"✔ Deployment initiated!", fg="green", bold=True)
        
        # 1. Console Link
        console_url = f"https://console.cloud.google.com/run/detail/{region}/{name}/metrics?project={client.project_id}"
        click.echo(f"\n{click.style('Cloud Run Console:', bold=True)} {console_url}")

        # 2. Tailored CLI Commands
        click.echo(f"\n{click.style('Useful Commands:', bold=True)}")
        
        base_cmd = "python3 src/cr_infer/cli/main.py"
        prj_flag = f"--project {client.project_id}"
        svc_flags = f"{name} --region {region}"
        
        click.echo(f"  Check Info:  {click.style(f'{base_cmd} services info {svc_flags} {prj_flag}', fg='cyan')}")
        click.echo(f"  View Logs:   {click.style(f'{base_cmd} services logs {svc_flags} {prj_flag} --follow', fg='cyan')}")
        click.echo(f"  Start Chat:  {click.style(f'{base_cmd} services chat {svc_flags} {prj_flag}', fg='cyan')}")
        
        click.echo("\nNote: It may take a few minutes for the GPU instance to start and the model to load.")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@cli.group()
def services():
    """Manage Cloud Run services"""
    pass

@services.command(name="list")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", help="GCP Region")
def services_list(project, region):
    """List Cloud Run services managed by cr-infer."""
    from cr_infer.deployer import CloudRunDeployer
    from cr_infer.config import list_supported_regions
    
    if not project:
        from google.auth import default
        _, default_project = default()
        project = prompt_if_missing(project, "Project ID", message=f"Enter Project ID (default: {default_project}):") or default_project

    region = prompt_if_missing(region, "Region", choices=list_supported_regions())
    
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    try:
        services = deployer.list_services(region)
        if not services:
            click.echo(f"No cr-infer managed services found in {region}.")
            return
        
        click.echo(f"Managed services in {region}:")
        for s in services:
            name = s["name"].split("/")[-1]
            status = "Ready" if s.get("reconciling") is False else "Updating"
            click.echo(f"- {click.style(name, fg='cyan')} [{status}] URL: {s.get('uri')}")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@services.command(name="info")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", required=True)
def service_info(name, project, region):
    """Show detailed information for a service."""
    from cr_infer.deployer import CloudRunDeployer
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    try:
        s = deployer.get_service(region, name)
        if not s:
            click.echo(f"Service '{name}' not found in {region}.")
            return
        
        click.echo(json.dumps(s, indent=2))
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@services.command(name="logs")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", required=True)
@click.option("--limit", type=int, default=20)
@click.option("--follow", "-f", is_flag=True, help="Stream logs in real-time")
def service_logs(name, project, region, limit, follow):
    """Fetch or stream logs for a service."""
    import time
    client = GCPClient(project_id=project)
    
    # Filter for Cloud Run service logs
    filter_str = f'resource.type="cloud_run_revision" resource.labels.service_name="{name}" resource.labels.location="{region}"'
    
    last_timestamp = None

    try:
        while True:
            current_filter = filter_str
            if last_timestamp:
                current_filter += f' timestamp > "{last_timestamp}"'

            entries = client.get_logs(current_filter, page_size=limit if not last_timestamp else 50)
            
            if entries:
                # Entries are ordered desc by default in our client, let's reverse to show chronological
                for entry in reversed(entries):
                    ts = entry.get("timestamp", "")
                    severity = entry.get("severity", "DEFAULT")
                    msg = entry.get("textPayload") or entry.get("jsonPayload") or entry.get("protoPayload")
                    color = "white"
                    if severity in ["ERROR", "CRITICAL"]: color = "red"
                    elif severity == "WARNING": color = "yellow"
                    
                    click.echo(f"[{click.style(ts, fg='cyan')}] [{click.style(severity, fg=color)}] {msg}")
                    last_timestamp = ts

            if not follow:
                if not entries:
                    click.echo(f"No logs found for service '{name}'.")
                break
            
            time.sleep(3)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@services.command(name="delete")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", required=True)
def service_delete(name, project, region):
    """Delete a Cloud Run service."""
    client = GCPClient(project_id=project)
    try:
        url = f"https://run.googleapis.com/v2/projects/{client.project_id}/locations/{region}/services/{name}"
        if click.confirm(f"Are you sure you want to delete service '{name}'?"):
            client.delete(url)
            click.secho(f"✔ Service '{name}' deletion initiated.", fg="green")
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

@services.command(name="chat")
@click.argument("name")
@click.option("--project", "-p", help="GCP Project ID")
@click.option("--region", "-r", required=True)
def service_chat(name, project, region):
    """Interactive chat with the deployed model."""
    from cr_infer.deployer import CloudRunDeployer
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    
    try:
        s = deployer.get_service(region, name)
        if not s:
            click.echo(f"Service '{name}' not found.")
            return
        
        url = s.get("uri")
        if not url:
            click.echo("Service URL not available yet. Is the service still deploying?")
            return

        click.echo(f"Connected to {click.style(name, fg='cyan')} at {url}")
        click.echo("Type 'exit' to quit.\n")

        # Get the ID token for authentication
        id_token = client.get_id_token(url)
        headers = {
            "Authorization": f"Bearer {id_token}",
            "Content-Type": "application/json"
        }

        # Determine model name from service config
        container = s["template"]["containers"][0]
        image = container["image"]
        is_ollama = "ollama" in image
        
        model_name = "default"
        if is_ollama:
            # Extract from MODEL env var
            model_name = next((e["value"] for e in container.get("env", []) if e["name"] == "MODEL"), "default")
        else:
            # For vLLM, it's often in --model arg
            model_arg = next((a for a in container.get("args", []) if a.startswith("--model=")), None)
            if model_arg:
                model_name = model_arg.split("=")[1]

        while True:
            prompt = click.prompt("You")
            if prompt.lower() in ["exit", "quit"]:
                break
            
            chat_url = f"{url}/api/generate" if is_ollama else f"{url}/v1/chat/completions"
            payload = {
                "model": model_name,
                "prompt": prompt,
                "stream": True
            } if is_ollama else {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True
            }

            try:
                # Use requests directly for streaming support
                import requests
                response = requests.post(chat_url, json=payload, headers=headers, timeout=120, stream=True)
                response.raise_for_status()
                
                click.echo(f"{click.style('Model', fg='green')}: ", nl=False)
                
                for line in response.iter_lines():
                    if not line:
                        continue
                    
                    line_str = line.decode('utf-8')
                    if is_ollama:
                        data = json.loads(line_str)
                        click.echo(data.get("response", ""), nl=False)
                        if data.get("done"):
                            break
                    else:
                        # OpenAI / vLLM style: 'data: {...}'
                        if line_str.startswith("data: "):
                            content = line_str[6:]
                            if content.strip() == "[DONE]":
                                break
                            data = json.loads(content)
                            delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                            click.echo(delta, nl=False)
                
                click.echo("\n")
            except Exception as e:
                click.secho(f"Chat error: {e}", fg="red")
                if hasattr(e, 'response') and e.response is not None:
                    click.echo(f"Details: {e.response.text}")

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")

if __name__ == "__main__":
    cli()
