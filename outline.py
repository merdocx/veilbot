import requests

def create_key(api_url, cert_sha256):
    try:
        response = requests.post(
            f"{api_url}/access-keys",
            verify=False,  # WARNING: Disables SSL certificate verification
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Ошибка при создании ключа: {e}")
        return None

def delete_key(api_url, cert_sha256, key_id):
    try:
        response = requests.delete(
            f"{api_url}/access-keys/{key_id}",
            verify=False, # WARNING: Disables SSL certificate verification
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Ошибка при удалении ключа: {e}")
        return False
