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
        print(f"Attempting to delete key {key_id} from {api_url}")
        response = requests.delete(
            f"{api_url}/access-keys/{key_id}",
            verify=False, # WARNING: Disables SSL certificate verification
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        print(f"Delete response status: {response.status_code}")
        if response.status_code != 200:
            print(f"Delete response text: {response.text}")
        response.raise_for_status()
        print(f"Successfully deleted key {key_id}")
        return True
    except Exception as e:
        print(f"Ошибка при удалении ключа {key_id}: {e}")
        return False
