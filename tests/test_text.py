from novel_selector.text import is_explicitly_complete, novel_fingerprint


def test_complete_status_must_be_explicit():
    assert is_explicitly_complete("玄幻, 完结")
    assert is_explicitly_complete("已完成")
    assert not is_explicitly_complete("")
    assert not is_explicitly_complete("玄幻")
    assert not is_explicitly_complete("连载中, 完结倒计时")


def test_fingerprint_normalizes_title_and_author():
    assert novel_fingerprint("《大道争锋》", "误道者") == novel_fingerprint("大道争锋", " 误道者 ")

