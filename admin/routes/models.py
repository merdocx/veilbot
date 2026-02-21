"""
Pydantic модели для валидации данных
"""
from pydantic import BaseModel, validator
import re


class ServerForm(BaseModel):
    """Модель валидации данных сервера"""
    name: str
    api_url: str
    cert_sha256: str = ""
    max_keys: int
    protocol: str = "outline"
    domain: str = ""
    api_key: str = ""
    v2ray_path: str = "/v2ray"
    
    @validator('name')
    def validate_name(cls, v):
        if len(v.strip()) < 1:
            raise ValueError('Name cannot be empty')
        return v.strip()
    
    @validator('api_url')
    def validate_api_url(cls, v):
        if not re.match(r'^https?://[^\s/$.?#].[^\s]*$', v):
            raise ValueError('Invalid URL format')
        return v.strip()
    
    @validator('cert_sha256')
    def validate_cert_sha256(cls, v):
        if v and not re.match(r'^[A-Fa-f0-9:]+$', v):
            raise ValueError('Invalid certificate SHA256 format')
        return v.strip() if v else ""
    
    @validator('max_keys')
    def validate_max_keys(cls, v):
        if v < 1:
            raise ValueError('Max keys must be at least 1')
        return v
    
    @validator('protocol')
    def validate_protocol(cls, v):
        if v not in ['outline', 'v2ray']:
            raise ValueError('Protocol must be either outline or v2ray')
        return v
    
    @validator('domain')
    def validate_domain(cls, v):
        if v in [None, "None", ""]:
            return ""
        value = v.strip()
        if not value:
            return ""
        # Разрешаем домен с TLD (example.com, veil-bot.ru)
        if re.match(r'^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', value):
            return value
        # Проверка IPv4 адреса
        ip_pattern = r'^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$'
        match = re.match(ip_pattern, value)
        if match:
            parts = [int(match.group(i)) for i in range(1, 5)]
            if all(0 <= part <= 255 for part in parts):
                return value
        # Разрешаем простой hostname без TLD (server22, my-v2ray-host)
        if re.match(r'^[a-zA-Z0-9.-]+$', value) and len(value) <= 253:
            return value
        raise ValueError('Недопустимый формат домена или IP')
    
    @validator('api_key')
    def validate_api_key(cls, v, values):
        # API key обязателен только для V2Ray серверов
        protocol = values.get('protocol', 'outline')
        if protocol == 'v2ray' and not v:
            raise ValueError('API key is required for V2Ray servers')
        return v
    
    @validator('v2ray_path')
    def validate_v2ray_path(cls, v):
        if v in [None, "None", ""]:
            return "/v2ray"
        if not v.startswith('/'):
            raise ValueError('V2Ray path must start with /')
        return v


class TariffForm(BaseModel):
    """Модель валидации данных тарифа"""
    name: str
    duration_sec: int
    price_rub: int
    
    @validator('name')
    def validate_name(cls, v):
        if len(v.strip()) < 1:
            raise ValueError('Name cannot be empty')
        return v.strip()
    
    @validator('duration_sec')
    def validate_duration(cls, v):
        if v < 1:
            raise ValueError('Duration must be at least 1 second')
        return v
    
    @validator('price_rub')
    def validate_price(cls, v):
        if v < 0:
            raise ValueError('Price cannot be negative')
        return v

