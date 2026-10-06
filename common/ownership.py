"""Position ownership protocol.

Classifies account holdings into strategy buckets using IB orderRef tags
(first) and execution clientId (fallback for untagged legacy positions).
"""

TAG_PREFIX = "tag:"

CLIENT_IDS: dict[str, int] = {
    "spy-steady": 2,
    "spy-turbo": 3,
    "momo": 100,
}

CLIENT_TO_STRATEGY: dict[int, str] = {v: k for k, v in CLIENT_IDS.items()}


def make_order_ref(strategy_id: str) -> str:
    return f"{TAG_PREFIX}{strategy_id}"


def parse_order_ref(ref: str | None) -> str | None:
    if ref and ref.startswith(TAG_PREFIX):
        return ref[len(TAG_PREFIX):]
    return None


class ExecutionView:
    """Ownership-protocol execution: contract (with conId) + clientId."""
    __slots__ = ("contract", "clientId")

    def __init__(self, contract, client_id):
        self.contract = contract
        self.clientId = client_id


def adapt_fills(fills: list) -> list:
    """Adapt ib_insync Fill objects (contract + execution.clientId) to the
    ownership protocol. ib_insync Execution objects themselves carry no
    conId; Fill carries both."""
    return [ExecutionView(f.contract, f.execution.clientId) for f in fills]


def classify_holdings(positions: list, executions: list | None = None) -> dict[str, list]:
    """Classify positions into per-strategy buckets plus an "unclaimed" bucket.

    Classification order per position:
      1. position-level orderRef tag (when the object exposes one)
      2. clientId of a same-conId execution (mapped via CLIENT_TO_STRATEGY)
      3. unclaimed
    """
    conid_to_client: dict[int, int] = {}
    for ex in executions or []:
        conid_to_client[ex.contract.conId] = ex.clientId

    out: dict[str, list] = {sid: [] for sid in CLIENT_IDS}
    out["unclaimed"] = []

    for pos in positions:
        sid = parse_order_ref(getattr(pos, "orderRef", None))
        if sid is None:
            client_id = conid_to_client.get(pos.contract.conId)
            sid = CLIENT_TO_STRATEGY.get(client_id) if client_id is not None else None
        out[sid if sid in CLIENT_IDS else "unclaimed"].append(pos)

    return out
