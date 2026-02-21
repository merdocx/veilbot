import pytest

from payments.services.payment_service import PaymentService


class FakeRepository:
    def __init__(self):
        self.created = None

    async def create(self, payment):
        self.created = payment


class FakeYooKassaService:
    def __init__(self, payment_id="yk-1", confirmation_url="https://pay.example/ok"):
        self.payment_id = payment_id
        self.confirmation_url = confirmation_url
        self.calls = []

    async def create_payment(self, **kwargs):
        self.calls.append(kwargs)
        return self.payment_id, self.confirmation_url


class FakeCryptoBotService:
    async def create_invoice(self, **kwargs):
        return "invoice-1", "https://pay.crypto/ok", "hash"


@pytest.mark.asyncio
async def test_create_payment_persists_record_and_returns_identifiers():
    repo = FakeRepository()
    yookassa = FakeYooKassaService()
    service = PaymentService(repo, yookassa)

    payment_id, confirmation_url = await service.create_payment(
        user_id=42,
        tariff_id=7,
        amount=19900,
        email="user@example.com",
        protocol="v2ray",
        description=None,
    )

    assert payment_id == yookassa.payment_id
    assert confirmation_url == yookassa.confirmation_url
    assert repo.created is not None
    assert repo.created.metadata["protocol"] == "v2ray"


@pytest.mark.asyncio
async def test_create_payment_returns_none_on_invalid_email():
    service = PaymentService(FakeRepository(), FakeYooKassaService())

    payment_id, url = await service.create_payment(
        user_id=1,
        tariff_id=1,
        amount=1000,
        email="invalid-email",
    )

    assert payment_id is None
    assert url is None


@pytest.mark.asyncio
async def test_create_crypto_payment_without_service_returns_none():
    service = PaymentService(FakeRepository(), FakeYooKassaService(), cryptobot_service=None)

    invoice_id, pay_url = await service.create_crypto_payment(
        user_id=5,
        tariff_id=9,
        amount_usd=10.0,
        email="user@example.com",
    )

    assert invoice_id is None
    assert pay_url is None

