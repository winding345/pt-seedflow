"""换链接重试逻辑的测试（不依赖网络与数据库）。"""
import time

import pytest

from app.scheduler import ServiceRunner
from app.sites.base import SiteError


class FlakySite:
    def __init__(self, fail_times=2, error="genDlToken 连接失败: [Errno 104] Connection reset by peer"):
        self.fail_times = fail_times
        self.error = error
        self.calls = 0

    def genlink(self, tid):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise SiteError(self.error)
        return "https://example.invalid/dl/%s" % tid


def make_runner(site):
    runner = object.__new__(ServiceRunner)
    runner.site = site
    return runner


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)


def test_retries_transient_connection_error():
    site = FlakySite(fail_times=2)
    assert make_runner(site)._genlink("42") == "https://example.invalid/dl/42"
    assert site.calls == 3


def test_gives_up_after_retries():
    site = FlakySite(fail_times=99)
    with pytest.raises(SiteError):
        make_runner(site)._genlink("42")
    assert site.calls == 3          # 首次 + 2 次重试


def test_business_error_is_not_retried():
    site = FlakySite(fail_times=99, error="m-team 业务错误 /api/torrent/genDlToken: 請求參數錯誤")
    with pytest.raises(SiteError):
        make_runner(site)._genlink("42")
    assert site.calls == 1
