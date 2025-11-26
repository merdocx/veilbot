import asyncio
from dataclasses import replace

import pytest

from payments.models.payment import Payment, PaymentStatus, PaymentProvider, PaymentCurrency
from payments.services.subscription_purchase_service import SubscriptionPurchaseService


class FakePaymentRepo:
    def __init__(self, payment: Payment | None):
        self.payment = payment
        self.calls = 0

    async def get_by_payment_id(self, payment_id: str):
        self.calls += 1
        return self.payment


class FakeTariffRepo:
    def get_tariff(self, _tariff_id):
        return (1, "V2Ray", 3600, 1000, 1024)


@pytest.mark.asyncio
async def test_process_subscription_purchase_missing_payment_returns_error(monkeypatch):
    service = SubscriptionPurchaseService(db_path=":memory:")
    service.payment_repo = FakePaymentRepo(payment=None)

    success, error = await service.process_subscription_purchase("not-found")

    assert success is False
    assert "not found" in (error or "")


@pytest.mark.asyncio
async def test_process_subscription_purchase_non_subscription_payment(monkeypatch):
    payment = Payment(
        payment_id="yk-1",
        user_id=1,
        tariff_id=1,
        amount=1000,
        metadata={"key_type": "outline"},
    )
    payment.status = PaymentStatus.PAID
    payment.protocol = "v2ray"

    service = SubscriptionPurchaseService(db_path=":memory:")
    service.payment_repo = FakePaymentRepo(payment=payment)

    success, error = await service.process_subscription_purchase("yk-1")

    assert success is False
    assert "not a subscription" in (error or "")


@pytest.mark.asyncio
async def test_process_subscription_purchase_wrong_protocol(monkeypatch):
    payment = Payment(
        payment_id="yk-2",
        user_id=1,
        tariff_id=1,
        amount=1000,
        metadata={"key_type": "subscription"},
    )
    payment.status = PaymentStatus.PAID
    payment.protocol = "outline"

    service = SubscriptionPurchaseService(db_path=":memory:")
    service.payment_repo = FakePaymentRepo(payment=payment)

    success, error = await service.process_subscription_purchase("yk-2")

    assert success is False
    assert "protocol is not v2ray" in (error or "")


@pytest.mark.asyncio
async def test_process_subscription_purchase_completed_payment_skips(monkeypatch):
    payment = Payment(
        payment_id="yk-3",
        user_id=1,
        tariff_id=1,
        amount=1000,
        metadata={"key_type": "subscription"},
    )
    payment.status = PaymentStatus.COMPLETED
    payment.protocol = "v2ray"

    service = SubscriptionPurchaseService(db_path=":memory:")
    service.payment_repo = FakePaymentRepo(payment=payment)

    success, error = await service.process_subscription_purchase("yk-3")

    assert success is True
    assert error is None

