from common.ownership import (make_order_ref, parse_order_ref, classify_holdings,
                              CLIENT_IDS)


class C:
    def __init__(self, conid=1): self.conId = conid

class Pos:
    def __init__(self, conid=1, ref=None):
        self.contract = C(conid); self.orderRef = ref
        self.position = 1; self.avgCost = 1.0

class Ex:
    def __init__(self, conid=1, client_id=2):
        self.contract = C(conid); self.clientId = client_id


def test_tag_roundtrip():
    assert make_order_ref("momo") == "tag:momo"
    assert parse_order_ref("tag:momo") == "momo"
    assert parse_order_ref(None) is None
    assert parse_order_ref("garbage") is None


def test_classify_by_order_ref():
    out = classify_holdings([Pos(ref="tag:momo")])
    assert len(out["momo"]) == 1
    assert out["unclaimed"] == []


def test_classify_by_client_id_regression_for_existing_256_spy_calls():
    # Today's real holding: 256 SPY 781C, no orderRef, executed by clientId 2
    p = Pos(conid=927852425, ref=None)
    out = classify_holdings([p], executions=[Ex(conid=927852425, client_id=2)])
    assert len(out["spy-steady"]) == 1
    assert out["unclaimed"] == []


def test_classify_unclaimed():
    out = classify_holdings([Pos(conid=5, ref=None)], executions=[])
    assert len(out["unclaimed"]) == 1


def test_unknown_client_id_is_unclaimed():
    out = classify_holdings([Pos(conid=7, ref=None)],
                            executions=[Ex(conid=7, client_id=999)])
    assert len(out["unclaimed"]) == 1
