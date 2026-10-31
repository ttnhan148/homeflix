from hometv import scoring
from hometv.health import HealthResult


def alive(**kw):
    return HealthResult(ok=True, **kw)


def test_dead_is_zero():
    assert scoring.score(HealthResult(ok=False, error="x", height=1080), True) == 0


def test_resolution_tiers():
    got = [scoring.score(alive(height=h), False) for h in (1080, 720, 480, 360, None)]
    assert got == [75, 68, 60, 54, 54]


def test_throughput_tiers_relative_to_bitrate():
    def s(kbps, bitrate):
        return scoring.score(alive(height=720, throughput_kbps=kbps, bitrate_kbps=bitrate), False)
    assert s(2000, 1000) == 68 + 15
    assert s(1000, 1000) == 68 + 8
    assert s(500, 1000) == 68
    assert s(5000, None) == 68


def test_https_bonus_and_cap():
    assert scoring.score(alive(height=720), True) == 78
    assert scoring.score(alive(height=1080, throughput_kbps=9000, bitrate_kbps=1000), True) == 100


def test_unknown_constant():
    assert scoring.UNKNOWN == 50
