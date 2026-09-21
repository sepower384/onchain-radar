from scanners.model import Candidate, StageResult
from scoring.engine import rank, score

W = {"accumulation": 0.30, "reaction": 0.25, "derivatives": 0.15,
     "revenue": 0.15, "news": 0.10, "security": 0.05}


def cand(**stages):
    c = Candidate(coin_key="cg:x", symbol="X")
    for k, v in stages.items():
        c.stages[k] = StageResult(score=v)
    return c


def test_없는_신호는_0점이_아니라_제외하고_재정규화():
    c = score(cand(reaction=80, derivatives=60, revenue=None, security=100), W)
    # (0.25*80 + 0.15*60 + 0.05*100) / 0.45
    assert c.score == round(34 / 0.45, 1)
    assert c.signals_used == 3


def test_없는_신호를_0점으로_치면_점수가_깎인다_그게_버그():
    wrong = (0.25 * 80 + 0.15 * 60 + 0 + 0.05 * 100) / (0.25 + 0.15 + 0.15 + 0.05)
    right = score(cand(reaction=80, derivatives=60, revenue=None, security=100), W).score
    assert right > wrong


def test_페널티는_가중합_뒤에_뺀다():
    c = cand(reaction=90, security=90)
    c.penalize("늦음", 25)
    assert score(c, W).score == 65.0


def test_점수는_0_100을_벗어나지_않는다():
    c = cand(reaction=10)
    c.penalize("과열", 50)
    assert score(c, W).score == 0.0


def test_신호가_너무_적으면_순위에서_제외():
    lone = cand(reaction=100)
    two = cand(reaction=70, security=80)
    out = rank([lone, two], W, min_signals=2)
    assert out == [two]


def test_필수_신호가_없으면_파생_안전_만점이어도_순위_제외():
    """버그 회귀: 반응 데이터 없이 파생100+안전100 으로 100점이 상위를 채웠다."""
    no_reaction = cand(derivatives=100, security=100)
    ok = cand(reaction=60, derivatives=80)
    out = rank([no_reaction, ok], W, min_signals=2, required=("reaction",))
    assert out == [ok]
