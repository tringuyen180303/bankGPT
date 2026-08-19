from copy import deepcopy

DEMO_USER = "teller"
DEMO_PASSWORD = "demo"

SEED = {
    "12345": {
        "name": "Alex Rivera",
        "status": "Active",
        "account_mask": "****4412",
        "savings": 1240.50,
        "has_credit": True,
        "credit_limit": 5000.00,
        "available_credit": 2200.00,
        "loan_balance": 2800.00,
    },
    "11111": {
        "name": "Jordan Lee",
        "status": "Active",
        "account_mask": "****0091",
        "savings": 88.00,
        "has_credit": False,
        "credit_limit": 0.0,
        "available_credit": 0.0,
        "loan_balance": 0.0,
    },
}

MEMBERS: dict = {}


def reset_members() -> None:
    MEMBERS.clear()
    MEMBERS.update(deepcopy(SEED))


def fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def parse_amount(raw: str) -> float | None:
    text = raw.replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return round(amount, 2)


def present(member: dict) -> dict:
    view = dict(member)
    view["savings_balance"] = fmt_money(member["savings"])
    view["credit_limit_display"] = fmt_money(member["credit_limit"])
    view["available_credit_display"] = fmt_money(member["available_credit"])
    view["loan_balance_display"] = fmt_money(member["loan_balance"])
    return view


reset_members()
