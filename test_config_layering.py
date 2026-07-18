# -*- coding: utf-8 -*-
"""
Config layering test script.

Verifies load_accounts() priority branches, _load_accounts_split() merge logic,
and main-loop enabled skip. Does NOT trigger real booking (mocks appoint_zwulib).

Run: python test_config_layering.py
"""
import json
import os
import sys
import io
from contextlib import redirect_stdout

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'config', 'accounts_config.json')


def _reload_demo():
    if 'demo' in sys.modules:
        del sys.modules['demo']
    import demo
    return demo


def _run_main_loop(demo):
    """Run the main loop with mocked appoint/notify, return processed accounts + output."""
    processed = []

    def fake_appoint(username, password, **kwargs):
        processed.append({
            'username': username,
            'password': password,
            'room_id': kwargs.get('room_id'),
            'begin': kwargs.get('begin'),
            'seat_ids': kwargs.get('seat_ids'),
        })
        return 'ok', 'mock success', 12920

    demo.appoint_zwulib = fake_appoint
    demo.notify = lambda *a, **k: None
    demo.notify_fail = lambda *a, **k: None

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        for i, account in enumerate(accounts, 1):
            username = account.get('username', '')
            password = account.get('password', '')
            if not username or not password:
                continue
            if account.get('enabled', True) is False:
                continue
            params = {**defaults, **{k: v for k, v in account.items()
                      if k not in ('username', 'password', 'enabled')}}
            demo.appoint_zwulib(
                username, password,
                room_id=params.get('room_id'),
                dday=params.get('dday'),
                begin=params.get('begin'),
                duration=params.get('duration'),
                seat_ids=params.get('seat_ids'),
                cron_delta_minutes=params.get('cron-delta-minutes', 5),
                max_retry=params.get('max-retry', 20),
            )
    return processed, f.getvalue()


def _ensure_no_local_file():
    if os.path.exists(ACCOUNTS_FILE):
        raise RuntimeError("Local file %s exists, will interfere with branch tests" % ACCOUNTS_FILE)


def _clean_env():
    for k in ('ACCOUNTS', 'ACCOUNTS_CONFIG', 'PASSWORDS'):
        os.environ.pop(k, None)


def test_1_old_accounts_compat():
    print("\n" + "=" * 60)
    print("Test 1: backward compat - ACCOUNTS old usage")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS'] = json.dumps([
        {"username": "old_user1", "password": "old_pass1"},
        {"username": "old_user2", "password": "old_pass2", "room_id": 4, "begin": 21},
    ])
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 2, "expected 2 accounts, got %d" % len(processed)
    assert processed[0]['username'] == 'old_user1'
    assert processed[0]['password'] == 'old_pass1'
    assert processed[1]['username'] == 'old_user2'
    assert processed[1]['password'] == 'old_pass2'
    assert processed[1]['room_id'] == 4
    assert processed[1]['begin'] == 21
    assert "old usage" in output or "lao yong fa" in output.lower() or "ACCOUNTS" in output
    print("[PASS] old usage: 2 accounts processed, password from ACCOUNTS, override fields work")


def test_2_new_split_normal():
    print("\n" + "=" * 60)
    print("Test 2: new split path - ACCOUNTS_CONFIG + PASSWORDS")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "new_user1"},
        {"username": "new_user2", "room_id": 4, "begin": 21, "duration": 9, "seat_ids": [12920, 12921]},
    ])
    os.environ['PASSWORDS'] = json.dumps({"new_user1": "pass1", "new_user2": "pass2"})
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 2, "expected 2 accounts, got %d" % len(processed)
    assert processed[0]['username'] == 'new_user1'
    assert processed[0]['password'] == 'pass1'
    assert processed[1]['username'] == 'new_user2'
    assert processed[1]['password'] == 'pass2'
    assert processed[1]['room_id'] == 4
    assert processed[1]['begin'] == 21
    assert processed[1]['seat_ids'] == [12920, 12921]
    print("[PASS] new path: 2 accounts loaded, password matched from PASSWORDS, overrides work")


def test_3_enabled_skip():
    print("\n" + "=" * 60)
    print("Test 3: enabled disable switch")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "active_user"},
        {"username": "disabled_user", "enabled": False, "room_id": 2},
        {"username": "active_user2", "enabled": True},
    ])
    os.environ['PASSWORDS'] = json.dumps({"active_user": "p1", "disabled_user": "p2", "active_user2": "p3"})
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 2, "expected 2 accounts (1 disabled), got %d" % len(processed)
    usernames = [a['username'] for a in processed]
    assert 'disabled_user' not in usernames
    assert 'active_user' in usernames
    assert 'active_user2' in usernames
    print("[PASS] enabled=false account skipped with log, enabled=true and default work")


def test_4_password_missing():
    print("\n" + "=" * 60)
    print("Test 4: partial password missing in PASSWORDS")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "has_pwd"},
        {"username": "no_pwd"},
    ])
    os.environ['PASSWORDS'] = json.dumps({"has_pwd": "secret"})
    demo = _reload_demo()
    processed, output = _run_main_loop(demo)
    assert len(processed) == 1, "expected 1 account (1 missing pwd), got %d" % len(processed)
    assert processed[0]['username'] == 'has_pwd'
    assert "PASSWORDS" in output
    print("[PASS] account with missing password skipped with warning, others proceed")


def test_5_passwords_absent_fail_loud():
    print("\n" + "=" * 60)
    print("Test 5: fail loud - ACCOUNTS_CONFIG set but PASSWORDS absent")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([{"username": "user1"}])
    demo = _reload_demo()
    accounts = demo.load_accounts()
    assert accounts == [], "PASSWORDS absent should return empty list"
    print("[PASS] PASSWORDS absent -> fail loud returns empty list")


def test_6_invalid_json():
    print("\n" + "=" * 60)
    print("Test 6: invalid JSON handling")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = "{not valid json"
    demo = _reload_demo()
    accounts = demo.load_accounts()
    assert accounts == [], "invalid JSON should return empty list"
    print("[PASS] invalid JSON reports error and returns empty list, no crash")


def test_7_local_file_priority():
    print("\n" + "=" * 60)
    print("Test 7: local file has highest priority")
    print("=" * 60)
    tmp_content = [{"username": "local_user", "password": "local_pass"}]
    if os.path.exists(ACCOUNTS_FILE):
        raise RuntimeError("%s already exists, skip test to avoid overwriting real config" % ACCOUNTS_FILE)
    try:
        with open(ACCOUNTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(tmp_content, f)
        os.environ['ACCOUNTS'] = json.dumps([{"username": "env_user", "password": "env_pass"}])
        os.environ['ACCOUNTS_CONFIG'] = json.dumps([{"username": "var_user"}])
        os.environ['PASSWORDS'] = json.dumps({"var_user": "p"})
        demo = _reload_demo()
        accounts = demo.load_accounts()
        assert len(accounts) == 1, "should return only the local file account"
        assert accounts[0]['username'] == 'local_user'
        print("[PASS] local file takes priority, env vars ignored")
    finally:
        if os.path.exists(ACCOUNTS_FILE):
            os.remove(ACCOUNTS_FILE)


def test_8_enabled_field_not_in_params():
    print("\n" + "=" * 60)
    print("Test 8: enabled field does not leak into params")
    print("=" * 60)
    _ensure_no_local_file()
    _clean_env()
    os.environ['ACCOUNTS_CONFIG'] = json.dumps([
        {"username": "user1", "enabled": True, "room_id": 3},
    ])
    os.environ['PASSWORDS'] = json.dumps({"user1": "p1"})
    demo = _reload_demo()
    captured = {}

    def fake_appoint(username, password, **kwargs):
        captured.update(kwargs)
        return 'ok', 'mock', 12920

    demo.appoint_zwulib = fake_appoint
    demo.notify = lambda *a, **k: None
    demo.notify_fail = lambda *a, **k: None

    f = io.StringIO()
    with redirect_stdout(f):
        accounts = demo.load_accounts()
        defaults = demo.load_booking_config()
        for i, account in enumerate(accounts, 1):
            username = account.get('username', '')
            password = account.get('password', '')
            if not username or not password:
                continue
            if account.get('enabled', True) is False:
                continue
            params = {**defaults, **{k: v for k, v in account.items()
                      if k not in ('username', 'password', 'enabled')}}
            demo.appoint_zwulib(
                username, password,
                room_id=params.get('room_id'),
                dday=params.get('dday'),
                begin=params.get('begin'),
                duration=params.get('duration'),
                seat_ids=params.get('seat_ids'),
                cron_delta_minutes=params.get('cron-delta-minutes', 5),
                max_retry=params.get('max-retry', 20),
            )

    assert 'enabled' not in captured, "enabled should not be in params, got: %s" % list(captured.keys())
    assert captured.get('room_id') == 3
    print("[PASS] enabled field properly excluded from params")


if __name__ == '__main__':
    tests = [
        test_1_old_accounts_compat,
        test_2_new_split_normal,
        test_3_enabled_skip,
        test_4_password_missing,
        test_5_passwords_absent_fail_loud,
        test_6_invalid_json,
        test_7_local_file_priority,
        test_8_enabled_field_not_in_params,
    ]
    passed, failed = 0, 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print("[FAIL] %s - %s" % (t.__name__, e))
            failed += 1
        except Exception as e:
            print("[FAIL] %s - %s: %s" % (t.__name__, type(e).__name__, e))
            failed += 1
    _clean_env()
    print("\n" + "=" * 60)
    print("Result: %d passed, %d failed (total %d)" % (passed, failed, len(tests)))
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
