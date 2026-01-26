import json
import sys
import click
from typing import Optional, List, Tuple, Dict, Any
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

def print_service_table(service_payload: Dict[str, Any], region: str, title: str = "Service Configuration"):
    """Generic helper to print a Cloud Run service object in a nice table."""
    W = 140
    click.echo("\n" + "╔" + "═" * (W - 2) + "╗")
    
    def print_row(label, value, color=None):
        inner_w = W - 6
        l_part = label.ljust(22)
        if isinstance(value, list):
            if not value:
                v_part = "None".ljust(inner_w - 22)
                click.echo(f"║  {click.style(l_part, fg='cyan')}{v_part}  ║")
            else:
                for i, item in enumerate(value):
                    v_part = str(item).ljust(inner_w - 22)
                    lbl = l_part if i == 0 else " ".ljust(22)
                    click.echo(f"║  {click.style(lbl, fg='cyan')}{v_part}  ║")
        else:
            v_part = str(value).ljust(inner_w - 22)
            v_styled = click.style(v_part, fg=color) if color else v_part
            click.echo(f"║  {click.style(l_part, fg='cyan')}{v_styled}  ║")

    # Title line
    click.echo(f"║  {click.style(title.ljust(W - 6), bold=True)}  ║")
    click.echo("╠" + "═" * (W - 2) + "╣")
    
    # Extract data from payload
    template = service_payload.get("template", {})
    container = template.get("containers", [{}])[0]
    resources = container.get("resources", {}).get("limits", {})
    scaling = template.get("scaling", {})
    
    # Mapping for readability
    name = service_payload.get("name", "").split("/")[-1]
    if not name: name = "[New Service]"

    print_row("Service Name:", name)
    print_row("Region:", region)
    
    if "updateTime" in service_payload:
        print_row("Last Updated:", service_payload["updateTime"])
    
    print_row("Container Image:", container.get("image", "Unknown"))
    print_row("GPU:", template.get("nodeSelector", {}).get("accelerator", "None"))
    print_row("vCPUs:", resources.get("cpu", "Default"))
    print_row("Memory:", resources.get("memory", "Default"))
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
@click.option("--region", "-r", help="Target region for GPU check")
@click.option("--token", "-t", help="HF Token (optional)")
@click.option("--wait/--no-wait", default=True, help="Wait for download to complete and stream logs (default)")
def model_download(project, source, model_id, bucket, region, token, wait):
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
        bucket_region = region
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
            if not bucket_region:
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
        
        W = 54 # Fixed outer width
        
        click.echo("\n" + "╔" + "═" * (W - 2) + "╗")
        
        def print_box_line(content, bold=False, cyan_label=None, color=None):
            # ║ (1) + space (2) + inner (W-6) + space (2) + ║ (1) = W
            inner_w = W - 6
            if cyan_label:
                # label (12) + content (inner_w - 12)
                l_part = cyan_label.ljust(12)
                v_part = content.ljust(inner_w - 12)
                line = f"║  {click.style(l_part, fg='cyan')}{v_part}  ║"
            else:
                text = content.ljust(inner_w)
                if bold: text = click.style(text, bold=True)
                if color: text = click.style(text, fg=color)
                line = f"║  {text}  ║"
            click.echo(line)

        print_box_line("Model Info Summary", bold=True)
        click.echo("╠" + "═" * (W - 2) + "╣")
        
        print_box_line(model_id, cyan_label="Model:")
        print_box_line(format_bytes(size_bytes), cyan_label="Total Size:")
        print_box_line(f"~{est_vram:.2f} GB", cyan_label="Est. vRAM:")
        print_box_line(bucket_region, cyan_label="Region:")
        
        print_box_line("")
        print_box_line("Compatible GPUs:", bold=True)
        
        from cr_infer.config import get_region_config
        region_cfg = get_region_config(bucket_region)
        if region_cfg:
            for g in region_cfg.gpus:
                status = "[v]" if g.vram_gb >= est_vram or size_bytes == 0 else "[x]"
                color = "green" if status == "[v]" else "red"
                gpu_text = f"{status} {g.name} ({g.vram_gb} GB)"
                print_box_line(gpu_text, color=color)
        else:
             print_box_line("No GPU info for this region", color="yellow")
        
        click.echo("╚" + "═" * (W - 2) + "╝\n")

        if not click.confirm("Start download?", default=True):
            return

        click.echo(f"Submitting Cloud Build download job...")
        build_id = start_download(client, source, model_id, bucket, token)
        
        console_url = f"https://console.cloud.google.com/cloud-build/builds/{build_id}?project={client.project_id}"
        click.secho(f"✔ Build started: {build_id}", fg="green", bold=True)
        click.echo(f"{click.style('Console Link:', bold=True)} {console_url}\n")
        
        if wait:
            click.echo("Waiting for build and streaming logs (Ctrl+C to stop waiting)...")
            import time
            last_logs = ""
            printed_waiting = False
            spinner_chars = ["|", "/", "-", "\\"]
            spinner_idx = 0
            
            while True:
                status = client.get_build_status(build_id)
                state = status.get("status")
                
                if state == "QUEUED":
                    if not printed_waiting:
                        click.echo("Build job is queued. Waiting for it to start...")
                        printed_waiting = True
                    # Show a small spinner for queued state
                    click.echo(f"\r {spinner_chars[spinner_idx % 4]} Queued...", nl=False)
                    spinner_idx += 1
                
                # Fetch logs from GCS if available
                log_url = status.get("logsBucket")
                if log_url:
                    bucket_name = log_url.replace("gs://", "").split("/")[0]
                    log_object = f"log-{build_id}.txt"
                    try:
                        logs = client.get_build_logs(bucket_name, log_object)
                        if logs != last_logs:
                            # Clear the spinner line if we were printing one
                            if printed_waiting:
                                click.echo("\r" + " " * 20 + "\r", nl=False)
                            
                            new_content = logs[len(last_logs):]
                            click.echo(new_content, nl=False)
                            last_logs = logs
                        elif state == "WORKING":
                            # Show a small spinner for working state if no new logs
                            click.echo(f"\r {spinner_chars[spinner_idx % 4]} Downloading...", nl=False)
                            spinner_idx += 1
                    except Exception:
                        # Silently wait for log object to be created
                        if state == "WORKING":
                            click.echo(f"\r {spinner_chars[spinner_idx % 4]} Initializing build...", nl=False)
                            spinner_idx += 1

                if state not in ["WORKING", "QUEUED"]:
                    # Clear any lingering spinner line
                    click.echo("\r" + " " * 30 + "\r", nl=False)
                    click.echo(f"\nBuild finished with status: {click.style(state, bold=True)}")
                    break
                time.sleep(1)
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

    # 1. Handle Model & Bucket Selection
    if not bucket and not model_id:
        from cr_infer.config import list_supported_regions
        gpu_regions = list_supported_regions()
        
        click.echo("No bucket or model specified. Scanning all buckets for available models...")
        all_buckets = client.list_buckets()
        
        choices = []
        mapping = {} # choice_str -> (model_id, bucket_name)
        
        for b in all_buckets:
            models_in_b = list_models_in_bucket(client, b["name"])
            for m in models_in_b:
                choice_str = f"{m['id']} (gs://{b['name']} in {b['location']})"
                choices.append(choice_str)
                mapping[choice_str] = (m["id"], b["name"])
        
        if not choices:
            click.echo("No managed models found in any bucket. Use 'cr-infer model download' first.")
            return
        
        selected = prompt_if_missing(None, "Model", choices=choices, message="Select model to deploy:")
        model_id, bucket = mapping[selected]

    elif not bucket:
        from cr_infer.config import list_supported_regions
        gpu_regions = list_supported_regions()
        all_buckets = client.list_buckets()
        valid_buckets = [b for b in all_buckets if b["location"] in gpu_regions]
        
        choices = [f"{b['name']} ({b['location']})" for b in valid_buckets]
        bucket_choice = prompt_if_missing(bucket, "Bucket", choices=choices, message="Select source bucket:")
        bucket = bucket_choice.split(" (")[0]

    # Find bucket region (needed if not already found in the 'both missing' branch)
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

        # 1. Build Payload
        payload = deployer.build_payload(
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

        # 2. Display Summary Table from the actual payload
        print_service_table(payload, region, title="Planned Deployment Configuration")

        # 3. Quota Check (Optional but helpful context)
        from cr_infer.quota import fetch_gpu_quota
        try:
            quotas = fetch_gpu_quota(client, region, gpu)
            q_val = quotas['non_zonal']
            q_color = "green" if q_val > 0 else "yellow"
            click.echo(f"  {click.style('Note:', bold=True)} Current Available Quota: {click.style(str(int(q_val)), fg=q_color)} (Without Zonal Redundancy)\n")
        except:
            pass

        if not click.confirm("Proceed with deployment?", default=True):
            return

        click.echo(f"Deploying service {name} to {region}...")
        op = deployer.deploy_service(name=name, region=region, payload=payload)
        
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
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON instead of a table")
def service_info(name, project, region, as_json):
    """Show detailed information for a service."""
    from cr_infer.deployer import CloudRunDeployer
    client = GCPClient(project_id=project)
    deployer = CloudRunDeployer(client)
    try:
        s = deployer.get_service(region, name)
        if not s:
            click.echo(f"Service '{name}' not found in {region}.")
            return
        
        if as_json:
            click.echo(json.dumps(s, indent=2))
        else:
            print_service_table(s, region, title=f"Service Details: {name}")
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

        # --- Readiness Check Loop ---
        import time
        click.echo(f"Waiting for {click.style(name, fg='cyan')} to be ready (this may take a few minutes if the model is loading)...")
        
        # Ollama root '/' might return 404 or nothing, '/api/tags' is more reliable
        health_url = f"{url}/api/tags" if is_ollama else f"{url}/v1/models"
        ready = False
        spinner_chars = ["|", "/", "-", "\\"]
        idx = 0
        
        while not ready:
            try:
                res = requests.get(health_url, headers=headers, timeout=5)
                if res.status_code == 200:
                    ready = True
                    click.echo("\r" + " " * 100 + "\r", nl=False) # Clear line
                    click.secho("✔ Service is ready!", fg="green")
                else:
                    click.echo(f"\r {spinner_chars[idx % 4]} Status: {res.status_code}. Waiting...", nl=False)
            except Exception as e:
                click.echo(f"\r {spinner_chars[idx % 4]} Connecting... ({type(e).__name__})", nl=False)
            
            if not ready:
                idx += 1
                time.sleep(2)
        # --- End Readiness Loop ---

        click.echo(f"Connected to {click.style(name, fg='cyan')} at {url}")
        click.echo("Type 'exit' to quit.\n")

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
