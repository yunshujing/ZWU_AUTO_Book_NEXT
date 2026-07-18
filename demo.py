import json
import os
import yaml
from zwulib import appoint_zwulib
from notice import notify, notify_fail

# ============================================
# 配置加载：账号从 accounts_config.json 读取，预约参数从 booking_config.yml 读取
# ============================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(BASE_DIR, 'config', 'accounts_config.json')
BOOKING_CONFIG_FILE = os.path.join(BASE_DIR, 'config', 'booking_config.yml')

# 内联默认值（兜底，booking_config.yml 也不存在时使用）
DEFAULTS = {
    'room_id': 2,
    'dday': 2,
    'begin': 12,
    'duration': 9,
    'seat_ids': [12920, 12921],
    'cron-delta-minutes': 5,
    'max-retry': 20,
    'notification_type': 'none',
    'sckey': '',
    'smtp': {},
}


def load_accounts():
    """
    加载账号列表，优先级:
    1. 本地 config/accounts_config.json（本地运行主方式）
    2. 环境变量 ACCOUNTS（老用法后向兼容，单 Secret 存完整 JSON 含密码）
    3. 环境变量 ACCOUNTS_CONFIG + PASSWORDS（新推荐用法，明文配置 + 密码映射）
    """
    # 1. 本地文件（本地运行主方式，不变）
    if os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
        if isinstance(cfg, list):
            return cfg
        return cfg.get('accounts', [])

    # 2. 老用法：ACCOUNTS 单 Secret（后向兼容，保留）
    accounts_json = os.environ.get('ACCOUNTS', '')
    if accounts_json:
        try:
            data = json.loads(accounts_json)
            if isinstance(data, list):
                print("提示: 检测到 ACCOUNTS Secret（老用法）。如需切换到新用法，"
                      "请删除 ACCOUNTS 并改用 ACCOUNTS_CONFIG (Variable) + PASSWORDS (Secret)。")
                return data
            if isinstance(data, dict) and 'accounts' in data:
                print("提示: 检测到 ACCOUNTS Secret（老用法）。如需切换到新用法，"
                      "请删除 ACCOUNTS 并改用 ACCOUNTS_CONFIG (Variable) + PASSWORDS (Secret)。")
                return data['accounts']
        except json.JSONDecodeError:
            print("警告: ACCOUNTS 环境变量 JSON 解析失败")

    # 3. 新用法：ACCOUNTS_CONFIG (Variable) + PASSWORDS (Secret)
    accounts_cfg_str = os.environ.get('ACCOUNTS_CONFIG', '')
    if accounts_cfg_str:
        return _load_accounts_split(accounts_cfg_str)

    # 都没命中
    print("错误: 未找到账号配置。请使用以下任一方式：")
    print("  1. 本地 config/accounts_config.json")
    print("  2. GitHub Secret ACCOUNTS（老用法）")
    print("  3. GitHub Variable ACCOUNTS_CONFIG + Secret PASSWORDS（推荐）")
    return []


def _load_accounts_split(accounts_cfg_str):
    """
    新用法加载：从 ACCOUNTS_CONFIG (明文) 读账号清单，从 PASSWORDS (Secret) 查密码。
    返回合并后的账号列表（每个账号含 username + password + 覆盖字段）。

    注意: enabled 停用检查由主循环统一处理，此处不做。
    """
    # 解析 ACCOUNTS_CONFIG
    try:
        accounts_cfg = json.loads(accounts_cfg_str)
    except json.JSONDecodeError as e:
        print(f"错误: ACCOUNTS_CONFIG JSON 解析失败: {e}")
        return []
    if isinstance(accounts_cfg, dict) and 'accounts' in accounts_cfg:
        accounts_cfg = accounts_cfg['accounts']
    if not isinstance(accounts_cfg, list):
        print("错误: ACCOUNTS_CONFIG 必须是 JSON 数组，或含 'accounts' 字段的对象")
        return []

    # 解析 PASSWORDS（fail loud: 设了 ACCOUNTS_CONFIG 却没设 PASSWORDS 是配置错误）
    passwords_str = os.environ.get('PASSWORDS', '')
    if not passwords_str:
        print("错误: 检测到 ACCOUNTS_CONFIG 但未设置 PASSWORDS Secret。"
              "新用法必须同时配置 PASSWORDS（{学号: 密码} JSON）。")
        return []
    try:
        passwords = json.loads(passwords_str)
    except json.JSONDecodeError as e:
        print(f"错误: PASSWORDS JSON 解析失败: {e}")
        return []
    if not isinstance(passwords, dict):
        print("错误: PASSWORDS 必须是 JSON 对象 {学号: 密码}")
        return []

    # 合并：遍历账号清单，用 username 查密码
    merged = []
    for acc in accounts_cfg:
        username = acc.get('username', '')
        if not username:
            print(f"跳过: 账号配置缺少 username 字段: {acc}")
            continue

        password = passwords.get(username, '')
        if not password:
            print(f"跳过: 账号 {username} 在 PASSWORDS 中未找到对应密码")
            continue

        # 组装完整账号：保留原账号所有字段（含 enabled，由主循环统一处理停用）+ 注入密码
        merged_acc = dict(acc)
        merged_acc['password'] = password
        merged.append(merged_acc)

    if not merged:
        print("警告: ACCOUNTS_CONFIG 中没有有效账号（全部密码缺失或缺少 username）")
    return merged


def load_booking_config():
    """
    加载默认预约参数（从仓库配置文件）
    """
    config = DEFAULTS.copy()

    if os.path.exists(BOOKING_CONFIG_FILE):
        with open(BOOKING_CONFIG_FILE, 'r', encoding='utf-8') as f:
            yml_cfg = yaml.safe_load(f) or {}
        # 加载所有配置项，不只限于 DEFAULTS 中的 key
        config.update(yml_cfg)

    # SCKEY 优先从环境变量读取（GitHub Secrets 覆盖）
    sckey = os.environ.get('SCKEY', '')
    if sckey:
        config['sckey'] = sckey

    # 有 SCKEY 但没配通知类型时，自动启用微信通知
    if config.get('sckey') and config.get('notification_type') == 'none':
        config['notification_type'] = 'wechat'

    return config


if __name__ == '__main__':
    accounts = load_accounts()
    defaults = load_booking_config()

    if not accounts:
        print("无账号配置，退出")
        exit(1)

    print(f"共 {len(accounts)} 个账号待预约")

    for i, account in enumerate(accounts, 1):
        username = account.get('username', '')
        password = account.get('password', '')

        if not username or not password:
            print(f"跳过第 {i} 个账号: 缺少 username 或 password")
            continue

        # 停用开关（所有加载路径统一生效，未配置 enabled 默认为启用）
        if account.get('enabled', True) is False:
            print(f"跳过第 {i} 个账号 {username}: 已停用 (enabled=false)")
            continue

        # 合并: 账号级覆盖 > 默认配置（排除 username/password/enabled 等非预约参数）
        params = {**defaults, **{k: v for k, v in account.items()
                  if k not in ('username', 'password', 'enabled')}}

        print(f"\n{'='*40}")
        print(f"预约第 {i}/{len(accounts)} 个账号: {username}")
        print(f"自习室:{params.get('room_id')} 开始:{params.get('begin')}:00 "
              f"时长:{params.get('duration')}h 座位:{params.get('seat_ids') or '随机'}")
        print(f"{'='*40}")

        stat, msg, seatid = appoint_zwulib(
            username, password,
            room_id=params.get('room_id'),
            dday=params.get('dday'),
            begin=params.get('begin'),
            duration=params.get('duration'),
            seat_ids=params.get('seat_ids'),
            cron_delta_minutes=params.get('cron-delta-minutes', 5),
            max_retry=params.get('max-retry', 20),
        )

        # 预约后通知
        if stat == "ok":
            try:
                notify(username, params.get('dday', 2), seatid, params)
            except Exception as e:
                print(f"通知发送失败: {e}")
        else:
            # 预约失败，发送失败原因
            try:
                notify_fail(username, msg, params)
            except Exception as e:
                print(f"失败通知发送失败: {e}")
