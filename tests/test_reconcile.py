from common.reconcile import reconcile
from tests.test_ownership import Pos, Ex


def test_mine_others_unclaimed_split():
    positions = [
        Pos(conid=1, ref="tag:momo"),
        Pos(conid=2, ref="tag:spy-steady"),
        Pos(conid=3, ref=None),
    ]
    mine, others, unclaimed = reconcile(
        "momo", positions, executions=[Ex(3, 999)])
    assert len(mine) == 1
    assert len(others) == 1
    assert len(unclaimed) == 1


def test_other_strategy_positions_ignored_not_error():
    mine, others, unclaimed = reconcile(
        "momo", [Pos(conid=2, ref="tag:spy-steady")], executions=[])
    assert mine == [] and len(others) == 1 and unclaimed == []
