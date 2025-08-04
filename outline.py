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

def get_keys(api_url, cert_sha256):
    """Получить все ключи с Outline сервера"""
    try:
        response = requests.get(
            f"{api_url}/access-keys",
            verify=False,  # WARNING: Disables SSL certificate verification
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        # API возвращает словарь с ключом accessKeys
        if isinstance(data, dict) and 'accessKeys' in data:
            return data['accessKeys']
        elif isinstance(data, list):
            return data
        else:
            print(f"Неожиданный формат ответа: {type(data)}")
            return []
    except Exception as e:
        print(f"Ошибка при получении ключей: {e}")
        return []

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
