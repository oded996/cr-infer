from typing import Optional, Tuple
from cr_infer.client import GCPClient
from InquirerPy import inquirer

HF_TOKEN_SECRET_NAME = "cr-infer-hf-token"

def ensure_hf_token(client: GCPClient, provided_token: Optional[str] = None) -> Tuple[Optional[str], bool]:
    """
    Ensures a Hugging Face token is available, using Secret Manager if possible.
    Returns (token_string, is_from_secret_manager).
    """
    # 1. If token is provided via flag, we offer to save it
    if provided_token:
        if inquirer.confirm(message=f"Do you want to save this token to Secret Manager as '{HF_TOKEN_SECRET_NAME}' for future use?", default=True).execute():
            _save_token(client, provided_token)
        return provided_token, False

    # 2. Check if secret exists in Secret Manager
    try:
        secret = client.get_secret(HF_TOKEN_SECRET_NAME)
        if secret:
            choices = [
                {"name": f"Use existing token from Secret Manager ({HF_TOKEN_SECRET_NAME})", "value": "existing"},
                {"name": "Provide a new token", "value": "new"},
                {"name": "Continue without a token", "value": "none"}
            ]
            choice = inquirer.select(
                message="Hugging Face token found in Secret Manager:",
                choices=choices
            ).execute()

            if choice == "existing":
                # When using existing, we don't necessarily need to access it here 
                # if we're just going to tell Cloud Build to use it.
                # However, we still need it for hf_preflight which runs locally.
                return client.access_secret(HF_TOKEN_SECRET_NAME), True
            elif choice == "none":
                return None, False
            # if "new", fall through to prompt
    except Exception as e:
        # Secret Manager might not be enabled or permissions might be missing
        # We'll just fall through to the manual prompt
        pass

    # 3. No token provided and no existing secret used, prompt user
    token = inquirer.secret(message="Enter Hugging Face Token (leave empty if not needed):").execute()
    if not token:
        return None, False

    # 4. Ask to save the newly provided token
    is_saved = False
    if inquirer.confirm(message=f"Do you want to save this token to Secret Manager as '{HF_TOKEN_SECRET_NAME}'?", default=True).execute():
        _save_token(client, token)
        is_saved = True
    
    return token, is_saved

def _save_token(client: GCPClient, token: str):
    try:
        if not client.get_secret(HF_TOKEN_SECRET_NAME):
            client.create_secret(HF_TOKEN_SECRET_NAME)
        client.add_secret_version(HF_TOKEN_SECRET_NAME, token)
        print(f"✔ Token saved to Secret Manager: {HF_TOKEN_SECRET_NAME}")
    except Exception as e:
        print(f"⚠ Failed to save token to Secret Manager: {e}")
        print("Continuing without saving.")
