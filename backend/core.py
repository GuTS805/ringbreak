"""Deterministic synthetic fixtures and balance-aware intervention replay."""
from dataclasses import dataclass

BASE = 1792252800000  # 2026-10-18 00:00 UTC, milliseconds
MINUTE = 60_000


@dataclass(frozen=True)
class Transfer:
    id: str
    case_id: str
    source: str
    target: str
    paise: int
    minute: int
    device: str = ""
    kind: str = "transfer"

    @property
    def at_ms(self):
        return BASE + self.minute * MINUTE


CASES = {
    "split": {"title": "Split → converge → cash-out", "flagged": "A01", "truth": "MULE_A and MULE_B forward to HUB; a HUB hold can reduce synthetic cash-out."},
    "shared": {"title": "Shared device, benign merchant", "flagged": "B01", "truth": "Shared device alone is insufficient; no hold is recommended."},
    "delayed": {"title": "Delayed forwarding", "flagged": "C01", "truth": "A hold now can block forwarding; a 30-minute delay misses it."},
    "empty": {"title": "No supporting evidence", "flagged": "D01", "truth": "No downstream path; no hold is recommended."},
}

INITIAL = {
    "VICTIM_A": 100_000, "MULE_A": 0, "MULE_B": 0, "HUB": 0, "CASH_A": 0,
    "CUSTOMER_B": 40_000, "MERCHANT_B": 0, "NEIGHBOR_B": 20_000,
    "VICTIM_C": 50_000, "RELAY_C": 0, "CASH_C": 0,
    "CUSTOMER_D": 10_000, "FRIEND_D": 0,
}

TRANSFERS = (
    Transfer("A01", "split", "VICTIM_A", "MULE_A", 30_000, 0, "dev-a"),
    Transfer("A02", "split", "VICTIM_A", "MULE_B", 25_000, 1, "dev-a"),
    Transfer("A03", "split", "MULE_A", "HUB", 28_000, 6, "dev-mule"),
    Transfer("A04", "split", "MULE_B", "HUB", 23_000, 8, "dev-mule"),
    Transfer("A05", "split", "HUB", "CASH_A", 45_000, 20, "dev-hub", "cashout"),
    Transfer("B01", "shared", "CUSTOMER_B", "MERCHANT_B", 5_000, 0, "shared-kiosk"),
    Transfer("B02", "shared", "NEIGHBOR_B", "MERCHANT_B", 2_000, 15, "shared-kiosk"),
    Transfer("C01", "delayed", "VICTIM_C", "RELAY_C", 30_000, 0, "dev-c"),
    Transfer("C02", "delayed", "RELAY_C", "CASH_C", 28_000, 20, "dev-c2", "cashout"),
    Transfer("D01", "empty", "CUSTOMER_D", "FRIEND_D", 1_000, 0, "dev-d"),
)


def replay(case_id: str, hold_account: str | None = None, hold_at_ms: int | None = None):
    """A hold blocks outbound transfers at or after its effective time."""
    balances = dict(INITIAL)
    accepted, blocked = [], []
    cashout = 0
    for tx in sorted((t for t in TRANSFERS if t.case_id == case_id), key=lambda t: (t.at_ms, t.id)):
        if hold_account == tx.source and hold_at_ms is not None and tx.at_ms >= hold_at_ms:
            blocked.append({"id": tx.id, "reason": "simulated_hold"})
            continue
        if balances[tx.source] < tx.paise:
            blocked.append({"id": tx.id, "reason": "insufficient_balance"})
            continue
        balances[tx.source] -= tx.paise
        balances[tx.target] += tx.paise
        accepted.append(tx.id)
        if tx.kind == "cashout":
            cashout += tx.paise
    assert min(balances.values()) >= 0
    assert sum(balances.values()) == sum(INITIAL.values())
    return {"accepted": accepted, "blocked": blocked, "cashout_paise": cashout, "balances": balances}


def compare(case_id: str, account: str, decision_ms: int):
    base = replay(case_id)
    now = replay(case_id, account, decision_ms)
    delayed = replay(case_id, account, decision_ms + 30 * MINUTE)
    return {"account": account, "baseline": base["cashout_paise"],
            "hold_now": now["cashout_paise"], "hold_30m": delayed["cashout_paise"],
            "simulated_difference_now": base["cashout_paise"] - now["cashout_paise"],
            "simulated_difference_30m": base["cashout_paise"] - delayed["cashout_paise"],
            "blocked_now": now["blocked"], "blocked_30m": delayed["blocked"]}
