import google.auth
from google.auth.transport.requests import AuthorizedSession
from typing import List, Tuple, Dict, Any, Optional

class GCPClient:
    def __init__(self, project_id: str = None):
        self.credentials, self.default_project_id = google.auth.default()
        self.project_id = project_id or self.default_project_id
        self.session = AuthorizedSession(self.credentials)

    def verify_auth(self) -> Tuple[bool, str]:
        try:
            if not self.credentials:
                return False, "Not authenticated. Run 'gcloud auth application-default login'."
            # Refresh if needed
            if not self.credentials.valid:
                from google.auth.transport.requests import Request
                self.credentials.refresh(Request())
            
            # Try to get identity from userinfo API
            response = self.session.get("https://www.googleapis.com/oauth2/v3/userinfo")
            if response.status_code == 200:
                identity = response.json().get("email")
            else:
                identity = getattr(self.credentials, 'service_account_email', None)
                if not identity and hasattr(self.credentials, 'signer_email'):
                    identity = self.credentials.signer_email
                
                if not identity:
                    # Fallback to gcloud config
                    import subprocess
                    try:
                        res = subprocess.run(["gcloud", "config", "get-value", "account"], capture_output=True, text=True)
                        if res.returncode == 0:
                            identity = res.stdout.strip()
                    except Exception:
                        pass
                
                if not identity:
                    identity = "User Credentials"

            return True, identity
        except Exception as e:
            return False, str(e)

    def check_permissions(self, permissions: List[str]) -> List[Tuple[str, bool]]:
        # Use v3 for more reliable permission testing on modern projects
        url = f"https://cloudresourcemanager.googleapis.com/v3/projects/{self.project_id}:testIamPermissions"
        body = {"permissions": permissions}
        try:
            response = self.session.post(url, json=body)
            if response.status_code == 200:
                granted = response.json().get("permissions", [])
                return [(p, p in granted) for p in permissions]
            else:
                return [(p, False) for p in permissions]
        except Exception:
            return [(p, False) for p in permissions]

    def check_api_enabled(self, service_name: str) -> bool:
        url = f"https://serviceusage.googleapis.com/v1/projects/{self.project_id}/services/{service_name}"
        response = self.session.get(url)
        if response.status_code == 200:
            return response.json().get("state") == "ENABLED"
        return False

    def get_quota_info(self, region: str, quota_id: str) -> Dict[str, Any]:
        """Fetch quota info from Cloud Quotas API."""
        # Cloud Quotas API uses global location for quotaInfos usually
        url = f"https://cloudquotas.googleapis.com/v1/projects/{self.project_id}/locations/global/services/run.googleapis.com/quotaInfos/{quota_id}"
        response = self.session.get(url)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 403:
            raise Exception("Permission Denied or API not enabled for Cloud Quotas. Please run: gcloud services enable cloudquotas.googleapis.com")
        elif response.status_code == 404:
            return {}
        else:
            response.raise_for_status()
            return {}

    def trigger_build(self, build_config: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a build to Cloud Build."""
        url = f"https://cloudbuild.googleapis.com/v1/projects/{self.project_id}/builds"
        response = self.session.post(url, json=build_config)
        if not response.ok:
            raise Exception(f"Cloud Build API Error {response.status_code}: {response.text}")
        return response.json()

    def patch(self, url: str, json: Dict[str, Any]) -> Dict[str, Any]:
        """Send a PATCH request."""
        response = self.session.patch(url, json=json)
        if not response.ok:
            raise Exception(f"GCP API Error {response.status_code}: {response.text}")
        return response.json()

    def delete(self, url: str) -> Dict[str, Any]:
        """Send a DELETE request."""
        response = self.session.delete(url)
        if not response.ok:
            raise Exception(f"GCP API Error {response.status_code}: {response.text}")
        return response.json()

    def get_build_status(self, build_id: str) -> Dict[str, Any]:
        """Fetch build details from Cloud Build."""
        url = f"https://cloudbuild.googleapis.com/v1/projects/{self.project_id}/locations/global/builds/{build_id}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json()

    def get_build_logs(self, bucket: str, log_object: str) -> str:
        """Fetch logs from GCS (Cloud Build logs)."""
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{log_object}?alt=media"
        response = self.session.get(url)
        if response.status_code == 200:
            return response.text
        response.raise_for_status()
        return ""

    def stream_build_logs(self, build_id: str, follow: bool = False):
        """Fetch and print logs for a Cloud Build task."""
        import time, sys
        printed_length = 0

        while True:
            try:
                build_info = self.get_build_status(build_id)
            except Exception as e:
                print(f"Error fetching build status: {e}")
                break

            status = build_info.get("status", "UNKNOWN")
            logs_bucket_uri = build_info.get("logsBucket", "")
            
            if logs_bucket_uri.startswith("gs://"):
                path = logs_bucket_uri[5:]
                parts = path.split("/", 1)
                bucket = parts[0]
                prefix = parts[1] if len(parts) > 1 else ""
                log_object = f"{prefix}/log-{build_id}.txt" if prefix else f"log-{build_id}.txt"

                try:
                    full_logs = self.get_build_logs(bucket, log_object)
                    if len(full_logs) > printed_length:
                        new_content = full_logs[printed_length:]
                        sys.stdout.write(new_content)
                        sys.stdout.flush()
                        printed_length = len(full_logs)
                except Exception:
                    pass

            if status in ["SUCCESS", "FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED"] or not follow:
                if not follow and printed_length == 0:
                    print(f"Build status: {status}. (Logs not available yet or could not be read from {logs_bucket_uri})")
                break

            time.sleep(3)

    def list_buckets(self) -> List[Dict[str, str]]:
        """List GCS buckets in the project with their locations."""
        url = f"https://storage.googleapis.com/storage/v1/b?project={self.project_id}"
        response = self.session.get(url)
        response.raise_for_status()
        items = response.json().get("items", [])
        return [{"name": b["name"], "location": b.get("location", "").lower()} for b in items]

    def create_bucket(self, name: str, location: str) -> Dict[str, Any]:
        """Create a new GCS bucket."""
        url = f"https://storage.googleapis.com/storage/v1/b?project={self.project_id}"
        body = {
            "name": name,
            "location": location,
            "storageClass": "STANDARD"
        }
        response = self.session.post(url, json=body)
        response.raise_for_status()
        return response.json()

    def get_gcs_prefix_size(self, bucket: str, prefix: str) -> int:
        """Calculate total size in bytes of objects in a GCS prefix."""
        url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?prefix={prefix}"
        total_bytes = 0
        while url:
            try:
                response = self.session.get(url)
                if not response.ok:
                    break
                data = response.json()
                for item in data.get("items", []):
                    total_bytes += int(item.get("size", 0))
                next_token = data.get("nextPageToken")
                if next_token:
                    url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?prefix={prefix}&pageToken={next_token}"
                else:
                    url = None
            except Exception:
                break
        return total_bytes

    def list_subnets(self, region: str) -> List[Dict[str, Any]]:
        """List subnets in a region."""
        url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/regions/{region}/subnetworks"
        response = self.session.get(url)
        if response.status_code == 200:
            return response.json().get("items", [])
        return []

    def patch_subnet(self, region: str, subnet_name: str, json: Dict[str, Any]) -> Dict[str, Any]:
        """Enable Private Google Access on a subnetwork."""
        # Compute Engine uses a specific POST method for Private Google Access
        url = f"https://compute.googleapis.com/compute/v1/projects/{self.project_id}/regions/{region}/subnetworks/{subnet_name}/setPrivateIpGoogleAccess"
        response = self.session.post(url, json=json)
        if response.status_code >= 400:
             raise Exception(f"Compute Engine API Error {response.status_code}: {response.text}")
        return response.json()

    def get_logs(self, filter_str: str, page_size: int = 50) -> List[Dict[str, Any]]:
        """Fetch logs from Cloud Logging."""
        url = "https://logging.googleapis.com/v2/entries:list"
        body = {
            "resourceNames": [f"projects/{self.project_id}"],
            "filter": filter_str,
            "orderBy": "timestamp desc",
            "pageSize": page_size
        }
        response = self.session.post(url, json=body)
        response.raise_for_status()
        return response.json().get("entries", [])

    def get_id_token(self, audience: str) -> str:
        """Generate an ID token for the given audience."""
        from google.auth.transport.requests import Request
        import subprocess

        try:
            # 1. Try built-in credentials (works for Service Accounts)
            if not self.credentials.valid:
                self.credentials.refresh(Request())
            
            if hasattr(self.credentials, 'id_token') and self.credentials.id_token:
                return self.credentials.id_token
            
            # 2. Fallback for User Credentials: Use gcloud to get an ID token
            # User credentials from 'gcloud auth application-default login' often don't provide an ID token directly.
            result = subprocess.run(
                ["gcloud", "auth", "print-identity-token", f"--audiences={audience}"],
                capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except Exception as e:
            raise Exception(f"Failed to obtain ID token: {e}")

    def request(self, method: str, url: str, **kwargs) -> Dict[str, Any]:
        response = self.session.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    def get_secret(self, secret_id: str) -> Optional[Dict[str, Any]]:
        """Fetch secret metadata."""
        url = f"https://secretmanager.googleapis.com/v1/projects/{self.project_id}/secrets/{secret_id}"
        response = self.session.get(url)
        if response.status_code == 200:
            return response.json()
        return None

    def create_secret(self, secret_id: str) -> Dict[str, Any]:
        """Create a new secret container."""
        url = f"https://secretmanager.googleapis.com/v1/projects/{self.project_id}/secrets?secretId={secret_id}"
        body = {"replication": {"automatic": {}}}
        response = self.session.post(url, json=body)
        response.raise_for_status()
        return response.json()

    def add_secret_version(self, secret_id: str, payload: str) -> Dict[str, Any]:
        """Add a new version to a secret."""
        import base64
        url = f"https://secretmanager.googleapis.com/v1/projects/{self.project_id}/secrets/{secret_id}:addVersion"
        body = {"payload": {"data": base64.b64encode(payload.encode()).decode()}}
        response = self.session.post(url, json=body)
        response.raise_for_status()
        return response.json()

    def get_project_number(self) -> str:
        """Get the numeric project number."""
        url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{self.project_id}"
        response = self.session.get(url)
        response.raise_for_status()
        return response.json().get("projectNumber")

    def grant_secret_access(self, secret_id: str, service_account_email: str):
        """Grant secretAccessor role to a service account on a specific secret."""
        url_get = f"https://secretmanager.googleapis.com/v1/projects/{self.project_id}/secrets/{secret_id}:getIamPolicy"
        policy_resp = self.session.get(url_get)
        policy_resp.raise_for_status()
        policy = policy_resp.json()
        
        bindings = policy.get("bindings", [])
        # Check if already granted
        for b in bindings:
            if b.get("role") == "roles/secretmanager.secretAccessor":
                if f"serviceAccount:{service_account_email}" in b.get("members", []):
                    return # Already granted
        
        bindings.append({
            "role": "roles/secretmanager.secretAccessor",
            "members": [f"serviceAccount:{service_account_email}"]
        })
        policy["bindings"] = bindings
        
        url_set = f"https://secretmanager.googleapis.com/v1/projects/{self.project_id}/secrets/{secret_id}:setIamPolicy"
        set_resp = self.session.post(url_set, json={"policy": policy})
        set_resp.raise_for_status()

    def access_secret(self, secret_id: str, version: str = "latest") -> str:
        """Access a secret version's value."""
        import base64
        url = f"https://secretmanager.googleapis.com/v1/projects/{self.project_id}/secrets/{secret_id}/versions/{version}:access"
        response = self.session.get(url)
        response.raise_for_status()
        data = response.json().get("payload", {}).get("data", "")
        return base64.b64decode(data).decode()