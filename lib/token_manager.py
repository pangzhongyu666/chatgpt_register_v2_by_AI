"""
Token 管理模块。
"""

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from .config import as_bool
from .utils import decode_jwt_payload

try:
    import aiohttp
except ImportError:
    aiohttp = None


_file_lock = threading.Lock()


class MiniPoolMaintainer:
    def __init__(self, base_url, token, target_type="codex", used_percent_threshold=95, user_agent=""):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.target_type = target_type
        self.used_percent_threshold = used_percent_threshold
        self.user_agent = user_agent or "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"

    def enabled(self):
        return bool(self.base_url and self.token)

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _safe_json(text):
        try:
            return json.loads(text)
        except Exception:
            return {}

    @staticmethod
    def _extract_account_id(item):
        for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
            val = item.get(key)
            if val:
                return str(val)
        return None

    @staticmethod
    def _item_type(item):
        return str(item.get("type") or item.get("typo") or "")

    def upload_token(self, filename, token_data, proxy=""):
        if not self.enabled():
            return False
        content = json.dumps(token_data, ensure_ascii=False).encode("utf-8")
        files = {"file": (filename, content, "application/json")}
        headers = {"Authorization": f"Bearer {self.token}"}
        proxies = {"http": proxy, "https": proxy} if proxy else None

        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/v0/management/auth-files",
                    files=files,
                    headers=headers,
                    timeout=30,
                    verify=False,
                    proxies=proxies,
                )
                if resp.status_code in (200, 201, 204):
                    return True
            except Exception:
                pass
            if attempt < 2:
                time.sleep(2 ** attempt)
        return False

    def fetch_auth_files(self, timeout=15):
        resp = requests.get(
            f"{self.base_url}/v0/management/auth-files",
            headers=self._headers(),
            timeout=timeout,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("files") if isinstance(data, dict) else []) or []

    async def probe_and_clean_async(self, workers=20, timeout=10, retries=1):
        if aiohttp is None:
            raise RuntimeError("需要安装 aiohttp: pip install aiohttp")

        files = self.fetch_auth_files(timeout)
        candidates = [
            item for item in files
            if self._item_type(item).lower() == self.target_type.lower()
        ]
        if not candidates:
            return {
                "total": len(files),
                "candidates": 0,
                "invalid_count": 0,
                "deleted_ok": 0,
                "deleted_fail": 0,
            }

        semaphore = asyncio.Semaphore(max(1, workers))
        connector = aiohttp.TCPConnector(limit=max(1, workers))
        client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))

        async def probe_one(session, item):
            auth_index = item.get("auth_index")
            name = item.get("name") or item.get("id")
            result = {
                "name": name,
                "auth_index": auth_index,
                "invalid_401": False,
                "used_up": False,
                "used_percent": None,
            }
            if not auth_index:
                return result

            account_id = self._extract_account_id(item)
            header = {
                "Authorization": "Bearer $TOKEN$",
                "Content-Type": "application/json",
                "User-Agent": self.user_agent,
            }
            if account_id:
                header["Chatgpt-Account-Id"] = account_id

            payload = {
                "authIndex": auth_index,
                "method": "GET",
                "url": "https://chatgpt.com/backend-api/wham/usage",
                "header": header,
            }

            for attempt in range(retries + 1):
                try:
                    async with semaphore:
                        async with session.post(
                            f"{self.base_url}/v0/management/api-call",
                            headers={**self._headers(), "Content-Type": "application/json"},
                            json=payload,
                            timeout=timeout,
                        ) as resp:
                            text = await resp.text()
                            if resp.status >= 400:
                                raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
                            data = self._safe_json(text)
                            status_code = data.get("status_code")
                            result["invalid_401"] = status_code == 401
                            if status_code == 200:
                                body = self._safe_json(data.get("body", ""))
                                used_pct = (
                                    body.get("rate_limit", {})
                                    .get("primary_window", {})
                                    .get("used_percent")
                                )
                                if used_pct is not None:
                                    result["used_percent"] = used_pct
                                    # 额度用完仅做标记，不参与清理，避免误删可刷新/可复用 token。
                                    result["used_up"] = used_pct >= self.used_percent_threshold
                            return result
                except Exception as e:
                    if attempt >= retries:
                        result["error"] = str(e)
                        return result
            return result

        async def delete_one(session, name):
            if not name:
                return False
            encoded = quote(name, safe="")
            try:
                async with semaphore:
                    async with session.delete(
                        f"{self.base_url}/v0/management/auth-files?name={encoded}",
                        headers=self._headers(),
                        timeout=timeout,
                    ) as resp:
                        text = await resp.text()
                        data = self._safe_json(text)
                        return resp.status == 200 and data.get("status") == "ok"
            except Exception:
                return False

        invalid_list = []
        used_up_list = []
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=client_timeout,
            trust_env=True,
        ) as session:
            tasks = [asyncio.create_task(probe_one(session, item)) for item in candidates]
            for task in asyncio.as_completed(tasks):
                result = await task
                if result.get("invalid_401"):
                    invalid_list.append(result)
                elif result.get("used_up"):
                    used_up_list.append(result)

            delete_tasks = [
                asyncio.create_task(delete_one(session, item.get("name")))
                for item in invalid_list if item.get("name")
            ]
            deleted_ok = 0
            deleted_fail = 0
            for task in asyncio.as_completed(delete_tasks):
                if await task:
                    deleted_ok += 1
                else:
                    deleted_fail += 1

        return {
            "total": len(files),
            "candidates": len(candidates),
            "invalid_count": len(invalid_list),
            "used_up_count": len(used_up_list),
            "deleted_ok": deleted_ok,
            "deleted_fail": deleted_fail,
        }

    def probe_and_clean_sync(self, workers=20, timeout=10, retries=1):
        return asyncio.run(self.probe_and_clean_async(workers, timeout, retries))


class TokenManager:
    """Token 管理器"""

    def __init__(self, config):
        self.config = config
        self.ak_file = config.get("ak_file", "ak.txt")
        self.rk_file = config.get("rk_file", "rk.txt")
        self.token_json_dir = config.get("token_json_dir", "tokens")
        self.upload_api_url = config.get("upload_api_url", "")
        self.upload_api_token = config.get("upload_api_token", "")
        self.proxy = config.get("proxy", "")
        self.cpa_base_url = config.get("cpa_base_url", "")
        self.cpa_token = config.get("cpa_token", "")
        self.cpa_workers = int(config.get("cpa_workers", 20) or 20)
        self.cpa_timeout = int(config.get("cpa_timeout", 12) or 12)
        self.cpa_retries = int(config.get("cpa_retries", 1) or 1)
        self.cpa_used_threshold = int(config.get("cpa_used_threshold", 95) or 95)
        self.cpa_upload_enabled = as_bool(config.get("cpa_upload", False))
        self.cpa_clean_enabled = as_bool(config.get("cpa_clean", False))
        self.cpa_prune_local = as_bool(config.get("cpa_prune_local", False))
        self.cpa_target_count = int(config.get("cpa_target_count", 0) or 0)
        self.cpa_user_agent = config.get(
            "cpa_user_agent",
            "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
        )

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.token_dir = self.token_json_dir if os.path.isabs(self.token_json_dir) else os.path.join(base_dir, self.token_json_dir)
        os.makedirs(self.token_dir, exist_ok=True)

        self.accounts_file = config.get("accounts_file", "accounts.txt")
        self.accounts_path = self.accounts_file if os.path.isabs(self.accounts_file) else os.path.join(base_dir, self.accounts_file)

        self.cpa_manager = None
        if self.cpa_base_url and self.cpa_token:
            self.cpa_manager = MiniPoolMaintainer(
                self.cpa_base_url,
                self.cpa_token,
                target_type="codex",
                used_percent_threshold=self.cpa_used_threshold,
                user_agent=self.cpa_user_agent,
            )

    def _build_token_data(self, email, tokens):
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        id_token = tokens.get("id_token", "")

        payload = decode_jwt_payload(access_token)
        auth_info = payload.get("https://api.openai.com/auth", {})
        account_id = auth_info.get("chatgpt_account_id", "")

        exp_timestamp = payload.get("exp")
        expired_str = ""
        if isinstance(exp_timestamp, int) and exp_timestamp > 0:
            exp_dt = datetime.fromtimestamp(exp_timestamp, tz=timezone(timedelta(hours=8)))
            expired_str = exp_dt.strftime("%Y-%m-%dT%H:%M:%S+08:00")

        now = datetime.now(tz=timezone(timedelta(hours=8)))
        return {
            "type": "codex",
            "email": email,
            "expired": expired_str,
            "id_token": id_token,
            "account_id": account_id,
            "access_token": access_token,
            "last_refresh": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "refresh_token": refresh_token,
        }

    def save_tokens(self, email, tokens, password=""):
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")

        if access_token:
            with _file_lock:
                with open(self.ak_file, "a", encoding="utf-8") as f:
                    f.write(f"{access_token}\n")

        if refresh_token:
            with _file_lock:
                with open(self.rk_file, "a", encoding="utf-8") as f:
                    f.write(f"{refresh_token}\n")

        if not access_token:
            return None

        token_data = self._build_token_data(email, tokens)
        token_path = os.path.join(self.token_dir, f"{email}.json")
        with _file_lock:
            with open(token_path, "w", encoding="utf-8") as f:
                json.dump(token_data, f, ensure_ascii=False, indent=2)

        if self.upload_api_url:
            self._upload_token_json(token_path)

        uploaded_to_cpa = False
        if self.cpa_upload_enabled:
            uploaded_to_cpa = self.upload_token_to_cpa(email, token_data)
            if uploaded_to_cpa and self.cpa_prune_local:
                self._prune_local_files(email, password, token_path)

        return {
            "token_path": token_path,
            "uploaded_to_cpa": uploaded_to_cpa,
        }

    def _upload_token_json(self, filepath):
        try:
            with open(filepath, "rb") as f:
                files = {"file": (os.path.basename(filepath), f, "application/json")}
                headers = {"Authorization": f"Bearer {self.upload_api_token}"}
                resp = requests.post(
                    self.upload_api_url,
                    files=files,
                    headers=headers,
                    verify=False,
                    timeout=30,
                )
                if resp.status_code == 200:
                    print("  [CPA] Token JSON 已上传到配置的上传接口")
                else:
                    print(f"  [CPA] 上传失败: {resp.status_code} - {resp.text[:200]}")
        except Exception as e:
            print(f"  [CPA] 上传异常: {e}")

    def upload_token_to_cpa(self, email, token_data):
        if not self.cpa_manager or not self.cpa_manager.enabled():
            print("  [CPA] 未配置 cpa_base_url / cpa_token，跳过上传")
            return False
        filename = f"token_{email.replace('@', '_')}_{int(time.time())}.json"
        ok = self.cpa_manager.upload_token(filename, token_data, proxy=self.proxy)
        if ok:
            print(f"  [CPA] 已上传 {filename} 到 CPA")
        else:
            print("  [CPA] 上传到 CPA 失败")
        return ok

    def count_valid_cpa_tokens(self):
        if not self.cpa_manager or not self.cpa_manager.enabled():
            return 0
        try:
            files = self.cpa_manager.fetch_auth_files(timeout=max(5, self.cpa_timeout))
            target = self.cpa_manager.target_type.lower()
            valid = [item for item in files if self.cpa_manager._item_type(item).lower() == target]
            return len(valid)
        except Exception as e:
            print(f"[CPA] 统计 token 失败: {e}")
            return 0

    def clean_invalid_cpa_tokens(self):
        if not self.cpa_clean_enabled:
            return None
        if not self.cpa_manager or not self.cpa_manager.enabled():
            print("[CPA] 未配置 cpa_base_url / cpa_token，跳过清理")
            return None
        try:
            result = self.cpa_manager.probe_and_clean_sync(
                workers=max(1, self.cpa_workers),
                timeout=max(5, self.cpa_timeout),
                retries=max(0, self.cpa_retries),
            )
            print(
                "[CPA] 清理完成: "
                f"total={result.get('total')} candidates={result.get('candidates')} "
                f"invalid={result.get('invalid_count')} used_up={result.get('used_up_count', 0)} "
                f"deleted_ok={result.get('deleted_ok')} "
                f"deleted_fail={result.get('deleted_fail')}"
            )
            return result
        except Exception as e:
            print(f"[CPA] 清理失败: {e}")
            return None

    def should_stop_for_cpa_target(self):
        if self.cpa_target_count <= 0:
            return False
        current_count = self.count_valid_cpa_tokens()
        print(f"[CPA] 当前有效 token: {current_count} / {self.cpa_target_count}")
        return current_count >= self.cpa_target_count

    def save_account(self, email, password, filepath=None):
        target = filepath or self.accounts_path
        with _file_lock:
            with open(target, "a", encoding="utf-8") as f:
                f.write(f"{email}----{password}\n")

    def remove_account_entry(self, email, password):
        if not os.path.exists(self.accounts_path):
            return
        try:
            with _file_lock:
                with open(self.accounts_path, "r", encoding="utf-8") as f:
                    lines = f.read().splitlines()
                target = f"{email}----{password}"
                kept = [line for line in lines if line.strip() != target]
                with open(self.accounts_path, "w", encoding="utf-8") as f:
                    if kept:
                        f.write("\n".join(kept) + "\n")
            print(f"[本地清理] 已从 accounts.txt 移除: {email}")
        except Exception as e:
            print(f"[本地清理] 移除账号行失败: {e}")

    def _prune_local_files(self, email, password, token_path):
        try:
            if token_path and os.path.exists(token_path):
                os.remove(token_path)
                print(f"[本地清理] 已删除 token 文件: {os.path.basename(token_path)}")
        except Exception as e:
            print(f"[本地清理] 删除 token 文件失败: {e}")

        if password:
            self.remove_account_entry(email, password)
