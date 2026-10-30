import pytest

_broken = []


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    assertion = call.excinfo is not None and call.excinfo.errisinstance(
        (AssertionError, pytest.fail.Exception)
    )
    if report.skipped or hasattr(report, "wasxfail"):
        _broken.append(item.nodeid)
    elif report.failed and (call.when != "call" or not assertion):
        _broken.append(item.nodeid)


def pytest_sessionfinish(session, exitstatus):
    if exitstatus in (0, 1) and _broken:
        session.exitstatus = 6
