"""
配置加载模块
"""

import json
import os


def load_config():
    """从 config.json 加载配置，环境变量优先级更高。"""
    config = {
        "total_accounts": 3,
        "concurrent_workers": 1,
        "skymail_admin_email": "",
        "skymail_admin_password": "",
        "skymail_domains": [],
        "proxy": "",
        "output_file": "registered_accounts.txt",
        "accounts_file": "accounts.txt",
        "csv_file": "registered_accounts.csv",
        "enable_oauth": True,
        "oauth_required": True,
        "oauth_issuer": "https://auth.openai.com",
        "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        "ak_file": "ak.txt",
        "rk_file": "rk.txt",
        "token_json_dir": "tokens",
        "upload_api_url": "",
        "upload_api_token": "",
        "cpa_base_url": "",
        "cpa_token": "",
        "cpa_workers": 20,
        "cpa_timeout": 12,
        "cpa_retries": 1,
        "cpa_used_threshold": 95,
        "cpa_clean": False,
        "cpa_upload": False,
        "cpa_target_count": 0,
        "cpa_prune_local": False,
        "cpa_user_agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
    }

    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.json",
    )
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
                config.update(file_config)
        except Exception as e:
            print(f"[WARN] 加载 config.json 失败: {e}")

    env_mappings = {
        "SKYMAIL_ADMIN_EMAIL": "skymail_admin_email",
        "SKYMAIL_ADMIN_PASSWORD": "skymail_admin_password",
        "PROXY": "proxy",
        "TOTAL_ACCOUNTS": "total_accounts",
        "CONCURRENT_WORKERS": "concurrent_workers",
        "ENABLE_OAUTH": "enable_oauth",
        "OAUTH_REQUIRED": "oauth_required",
        "OAUTH_ISSUER": "oauth_issuer",
        "OAUTH_CLIENT_ID": "oauth_client_id",
        "OAUTH_REDIRECT_URI": "oauth_redirect_uri",
        "AK_FILE": "ak_file",
        "RK_FILE": "rk_file",
        "TOKEN_JSON_DIR": "token_json_dir",
        "UPLOAD_API_URL": "upload_api_url",
        "UPLOAD_API_TOKEN": "upload_api_token",
        "CPA_BASE_URL": "cpa_base_url",
        "CPA_TOKEN": "cpa_token",
        "CPA_WORKERS": "cpa_workers",
        "CPA_TIMEOUT": "cpa_timeout",
        "CPA_RETRIES": "cpa_retries",
        "CPA_USED_THRESHOLD": "cpa_used_threshold",
        "CPA_CLEAN": "cpa_clean",
        "CPA_UPLOAD": "cpa_upload",
        "CPA_TARGET_COUNT": "cpa_target_count",
        "CPA_PRUNE_LOCAL": "cpa_prune_local",
        "CPA_USER_AGENT": "cpa_user_agent",
    }

    int_keys = {
        "total_accounts",
        "concurrent_workers",
        "cpa_workers",
        "cpa_timeout",
        "cpa_retries",
        "cpa_used_threshold",
        "cpa_target_count",
    }
    bool_keys = {
        "enable_oauth",
        "oauth_required",
        "cpa_clean",
        "cpa_upload",
        "cpa_prune_local",
    }

    for env_key, config_key in env_mappings.items():
        env_value = os.environ.get(env_key)
        if env_value is None:
            continue
        if config_key in int_keys:
            config[config_key] = int(env_value)
        elif config_key in bool_keys:
            config[config_key] = as_bool(env_value)
        else:
            config[config_key] = env_value

    return config


def as_bool(value):
    """将值转换为布尔值。"""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}
