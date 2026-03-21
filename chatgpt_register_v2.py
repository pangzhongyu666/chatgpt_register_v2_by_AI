"""
ChatGPT 批量自动注册工具 v2.0。
使用 Skymail 临时邮箱，并发自动注册 ChatGPT 账号。
"""

import argparse
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

from lib.chatgpt_client import ChatGPTClient
from lib.config import as_bool, load_config
from lib.oauth_client import OAuthClient
from lib.skymail_client import init_skymail_client
from lib.token_manager import TokenManager
from lib.utils import generate_random_birthday, generate_random_name, generate_random_password

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

_write_lock = threading.Lock()


def append_output_line(filepath, line):
    with _write_lock:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def register_one_account(idx, total, skymail_client, token_manager, oauth_client, config, max_retries=3):
    """注册单个账号。"""
    tag = f"[{idx}/{total}]"

    for attempt in range(max_retries):
        if attempt > 0:
            print(f"\n{tag} 重试注册 (尝试 {attempt + 1}/{max_retries})...")
            time.sleep(1)
        else:
            print(f"\n{tag} 开始注册...")

        try:
            print(f"{tag} 创建 Skymail 临时邮箱...")
            email, _mailbox_password = skymail_client.create_temp_email()
            print(f"{tag} 邮箱: {email}")

            password = generate_random_password()
            first_name, last_name = generate_random_name()
            birthdate = generate_random_birthday()
            print(f"{tag} 密码: {password}")
            print(f"{tag} 姓名: {first_name} {last_name}")

            proxy = config.get("proxy", "")
            chatgpt_client = ChatGPTClient(proxy=proxy, verbose=True)

            print(f"{tag} 开始注册流程...")
            success, msg = chatgpt_client.register_complete_flow(
                email, password, first_name, last_name, birthdate, skymail_client
            )
            if not success:
                is_tls_error = "TLS" in msg or "SSL" in msg or "curl: (35)" in msg
                if is_tls_error and attempt < max_retries - 1:
                    print(f"{tag} [WARN] TLS 错误，准备重试: {msg}")
                    continue
                print(f"{tag} [ERROR] 注册失败: {msg}")
                return False, email, password, msg

            print(f"{tag} [OK] 注册成功")

            enable_oauth = as_bool(config.get("enable_oauth", True))
            oauth_required = as_bool(config.get("oauth_required", True))
            output_file = config.get("output_file", "registered_accounts.txt")

            if enable_oauth:
                print(f"{tag} 开始 OAuth 登录...")
                oauth_client_reuse = OAuthClient(config, proxy=proxy, verbose=True)
                oauth_client_reuse.session = chatgpt_client.session
                tokens = oauth_client_reuse.login_and_get_tokens(
                    email,
                    password,
                    chatgpt_client.device_id,
                    chatgpt_client.ua,
                    chatgpt_client.sec_ch_ua,
                    chatgpt_client.impersonate,
                    skymail_client,
                )

                if tokens and tokens.get("access_token"):
                    print(f"{tag} [OK] OAuth 成功")
                    token_manager.save_account(email, password)
                    token_manager.save_tokens(email, tokens, password=password)
                    append_output_line(output_file, f"{email}----{password}----oauth=ok")

                    if token_manager.cpa_clean_enabled:
                        token_manager.clean_invalid_cpa_tokens()

                    return True, email, password, "注册成功 + OAuth 成功"

                print(f"{tag} [WARN] OAuth 失败")
                if oauth_required:
                    if attempt < max_retries - 1:
                        print(f"{tag} OAuth 失败，准备重试整个流程...")
                        continue
                    return False, email, password, "OAuth 失败（必需）"

                append_output_line(output_file, f"{email}----{password}----oauth=failed")
                token_manager.save_account(email, password)
                return True, email, password, "注册成功（OAuth 失败）"

            append_output_line(output_file, f"{email}----{password}")
            token_manager.save_account(email, password)
            return True, email, password, "注册成功"

        except Exception as e:
            error_msg = str(e)
            is_tls_error = "TLS" in error_msg or "SSL" in error_msg or "curl: (35)" in error_msg
            if is_tls_error and attempt < max_retries - 1:
                print(f"{tag} [WARN] 异常 (TLS 错误)，准备重试: {error_msg[:100]}")
                continue

            print(f"{tag} [ERROR] 注册失败: {e}")
            import traceback
            traceback.print_exc()
            return False, "", "", str(e)

    return False, "", "", "重试次数已用尽"


def main():
    parser = argparse.ArgumentParser(description="ChatGPT 批量自动注册工具 v2.0")
    parser.add_argument("-n", "--num", type=int, default=1, help="注册账号数量")
    parser.add_argument("-w", "--workers", type=int, default=1, help="并发线程数")
    parser.add_argument("--no-oauth", action="store_true", help="禁用 OAuth 登录")
    args = parser.parse_args()

    print("=" * 60)
    print("  ChatGPT 批量自动注册工具 v2.0 (模块化版本)")
    print("  使用 Skymail 临时邮箱")
    print("=" * 60)

    config = load_config()
    total_accounts = args.num
    max_workers = args.workers
    if args.no_oauth:
        config["enable_oauth"] = False

    skymail_client = init_skymail_client(config)
    token_manager = TokenManager(config)
    oauth_client = OAuthClient(config, proxy=config.get("proxy", ""), verbose=True)

    output_file = config.get("output_file", "registered_accounts.txt")
    enable_oauth = as_bool(config.get("enable_oauth", True))

    print("\n配置信息:")
    print(f"  注册数量: {total_accounts}")
    print(f"  并发数: {max_workers}")
    print(f"  输出文件: {output_file}")
    print(f"  Skymail API: {skymail_client.api_base}")
    print(f"  Token 目录: {token_manager.token_dir}")
    print(f"  启用 OAuth: {enable_oauth}")
    if token_manager.cpa_manager and token_manager.cpa_manager.enabled():
        print(f"  CPA Base URL: {token_manager.cpa_base_url}")
        print(f"  CPA 自动上传: {token_manager.cpa_upload_enabled}")
        print(f"  CPA 自动清理: {token_manager.cpa_clean_enabled}")
        if token_manager.cpa_target_count > 0:
            print(f"  CPA 目标数量: {token_manager.cpa_target_count}")
    print()

    if token_manager.cpa_clean_enabled:
        token_manager.clean_invalid_cpa_tokens()

    if token_manager.should_stop_for_cpa_target():
        print("[CPA] 已达到目标 token 数量，跳过本次注册。")
        return

    success_count = 0
    failed_count = 0
    start_time = time.time()

    if max_workers == 1:
        for i in range(1, total_accounts + 1):
            if token_manager.should_stop_for_cpa_target():
                print("[CPA] 已达到目标 token 数量，提前结束。")
                break

            success, email, password, msg = register_one_account(
                i, total_accounts, skymail_client, token_manager, oauth_client, config
            )
            if success:
                success_count += 1
            else:
                failed_count += 1
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for i in range(1, total_accounts + 1):
                future = executor.submit(
                    register_one_account,
                    i,
                    total_accounts,
                    skymail_client,
                    token_manager,
                    oauth_client,
                    config,
                )
                futures.append(future)

            for future in as_completed(futures):
                try:
                    success, email, password, msg = future.result()
                    if success:
                        success_count += 1
                    else:
                        failed_count += 1
                except Exception as e:
                    print(f"[ERROR] 任务异常: {e}")
                    failed_count += 1

    end_time = time.time()
    total_time = end_time - start_time

    print("\n" + "=" * 60)
    print("注册完成！")
    print(f"  成功: {success_count}")
    print(f"  失败: {failed_count}")
    print(f"  总计: {success_count + failed_count}")
    print(f"  总耗时: {total_time:.1f}s")
    if success_count > 0:
        print(f"  平均耗时: {total_time / max(success_count + failed_count, 1):.1f}s/账号")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n程序异常: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
