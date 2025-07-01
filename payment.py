from yookassa import Payment, Configuration
from config import YOOKASSA_SHOP_ID, YOOKASSA_API_KEY, YOOKASSA_RETURN_URL

# Ensure shop ID is a string and add debugging
print(f"Configuring Yookassa with Shop ID: {YOOKASSA_SHOP_ID}, API Key: {YOOKASSA_API_KEY[:10] if YOOKASSA_API_KEY else 'None'}...")

Configuration.account_id = str(YOOKASSA_SHOP_ID)
Configuration.secret_key = YOOKASSA_API_KEY

def create_payment(amount_rub: int, description: str, email: str):
    try:
        print(f"Creating payment: amount={amount_rub}, description={description}, email={email}")
        print(f"Using return URL: {YOOKASSA_RETURN_URL}")
        
        payment_data = {
            "amount": {
                "value": f"{amount_rub:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": YOOKASSA_RETURN_URL
            },
            "capture": True,
            "description": description,
            "receipt": {
                "customer": {
                    "email": email
                },
                "items": [
                    {
                        "description": description,
                        "quantity": 1.0,
                        "amount": {
                            "value": f"{amount_rub:.2f}",
                            "currency": "RUB"
                        },
                        "vat_code": 1,  # 1 = no VAT
                        "payment_mode": "full_prepayment",
                        "payment_subject": "service"
                    }
                ]
            }
        }
        
        print(f"Payment data: {payment_data}")
        
        payment = Payment.create(payment_data)
        if payment and payment.confirmation:
            print(f"Payment created successfully: {payment.id}")
            return payment.id, payment.confirmation.confirmation_url
        print("Payment created but no confirmation URL")
        return None, None
    except Exception as e:
        print(f"Ошибка при создании платежа: {e}")
        print(f"Error type: {type(e)}")
        print(f"Error details: {str(e)}")
        return None, None

def check_payment(payment_id: str):
    try:
        payment = Payment.find_one(payment_id)
        return payment.status == "succeeded"
    except Exception as e:
        print(f"Ошибка при проверке платежа: {e}")
        return False
