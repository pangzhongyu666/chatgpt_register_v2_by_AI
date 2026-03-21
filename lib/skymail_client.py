"""
Skymail mailbox client.
"""

import random
import re
import string
import sys
import time

import requests


class SkymailClient:
    """Skymail 邮箱服务客户端"""

    def __init__(self, admin_email, admin_password, api_base=None, proxy=None, domains=None):
        self.admin_email = admin_email
        self.admin_password = admin_password

        if api_base:
            self.api_base = api_base.rstrip("/")
        elif admin_email and "@" in admin_email:
            self.api_base = f"https://{admin_email.split('@')[1]}"
        else:
            self.api_base = ""

        self.proxy = proxy
        self.api_token = None

        if not domains or not isinstance(domains, list):
            raise Exception("未配置 skymail_domains，请在 config.json 中设置域名列表")
        self.domains = domains

    def _build_session(self):
        session = requests.Session()
        session.trust_env = False
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}
        return session

    def _request(self, method, url, **kwargs):
        """优先按当前代理配置请求，代理失败时回退为直连。"""
        session = self._build_session()
        try:
            return session.request(method, url, **kwargs)
        except requests.exceptions.ProxyError:
            direct_session = requests.Session()
            direct_session.trust_env = False
            return direct_session.request(method, url, **kwargs)

    def generate_token(self):
        """自动生成 Skymail API Token"""
        if not self.admin_email or not self.admin_password:
            print("[WARN] 未配置 Skymail 管理员账号")
            return None

        if not self.api_base:
            print("[WARN] 无法从管理员邮箱提取 API 域名")
            return None

        try:
            res = self._request(
                "POST",
                f"{self.api_base}/api/public/genToken",
                json={
                    "email": self.admin_email,
                    "password": self.admin_password,
                },
                headers={"Content-Type": "application/json"},
                timeout=15,
                verify=False,
            )

            if res.status_code == 200:
                data = res.json()
                if data.get("code") == 200:
                    token = data.get("data", {}).get("token")
                    if token:
                        print("[OK] 成功生成 Skymail API Token")
                        self.api_token = token
                        return token

            print(f"[WARN] 生成 Skymail Token 失败: {res.status_code} - {res.text[:200]}")
        except Exception as e:
            print(f"[WARN] 生成 Skymail Token 异常: {e}")

        return None

    def create_temp_email(self):
        """
        创建 Skymail 临时邮箱。

        这里必须调用 Skymail 的建号接口，否则只是在本地拼接了一个邮箱字符串，
        后台并不会真的出现这个用户。

        Returns:
            tuple: (email, mailbox_password)
        """
        if not self.api_token:
            raise Exception("SKYMAIL_API_TOKEN 未设置，无法创建临时邮箱")

        try:
            domain = random.choice(self.domains)
            prefix_length = random.randint(6, 10)
            prefix = "".join(random.choices(string.ascii_lowercase + string.digits, k=prefix_length))
            email = f"{prefix}@{domain}"
            mailbox_password = "".join(
                random.choices(string.ascii_letters + string.digits, k=16)
            )

            res = self._request(
                "POST",
                f"{self.api_base}/api/public/addUser",
                json={
                    "list": [
                        {
                            "email": email,
                            "password": mailbox_password,
                        }
                    ]
                },
                headers={
                    "Authorization": self.api_token,
                    "Content-Type": "application/json",
                },
                timeout=15,
                verify=False,
            )

            if res.status_code != 200:
                raise Exception(f"HTTP {res.status_code}: {res.text[:200]}")

            data = res.json()
            if data.get("code") != 200:
                raise Exception(data.get("message") or data.get("msg") or str(data)[:200])

            return email, mailbox_password
        except Exception as e:
            raise Exception(f"Skymail 创建邮箱失败: {e}")

    def fetch_emails(self, email):
        """从 Skymail 获取邮件列表"""
        try:
            res = self._request(
                "POST",
                f"{self.api_base}/api/public/emailList",
                json={
                    "toEmail": email,
                    "timeSort": "desc",
                    "num": 1,
                    "size": 20,
                },
                headers={
                    "Authorization": self.api_token,
                    "Content-Type": "application/json",
                },
                timeout=15,
                verify=False,
            )

            if res.status_code == 200:
                data = res.json()
                if data.get("code") == 200:
                    return data.get("data", [])
            return []
        except Exception:
            return []

    def extract_verification_code(self, content):
        """从邮件内容提取 6 位验证码"""
        if not content:
            return None

        patterns = [
            r"Verification code:?\s*(\d{6})",
            r"code is\s*(\d{6})",
            r"代码[:：]?\s*(\d{6})",
            r"验证码[:：]?\s*(\d{6})",
            r">\s*(\d{6})\s*<",
            r"(?<![#&])\b(\d{6})\b",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for code in matches:
                if code == "177010":
                    continue
                return code
        return None

    def wait_for_verification_code(self, email, timeout=30, exclude_codes=None):
        """等待验证邮件并提取验证码"""
        if exclude_codes is None:
            exclude_codes = set()

        if not hasattr(self, "_used_codes"):
            self._used_codes = set()
        all_exclude_codes = exclude_codes | self._used_codes

        print(f"  [WAIT] 等待验证码 (最大 {timeout}s)...")

        start = time.time()
        last_email_ids = set()

        while time.time() - start < timeout:
            emails = self.fetch_emails(email)

            if emails:
                for item in emails:
                    if not isinstance(item, dict):
                        continue

                    email_id = item.get("emailId")
                    if not email_id or email_id in last_email_ids:
                        continue

                    last_email_ids.add(email_id)

                    content = item.get("content") or item.get("text") or ""
                    code = self.extract_verification_code(content)

                    if code and code not in all_exclude_codes:
                        print(f"  [OK] 验证码: {code}")
                        self._used_codes.add(code)
                        return code

            elapsed = time.time() - start
            if elapsed < 10:
                time.sleep(0.5)
            else:
                time.sleep(2)

        print("  [TIMEOUT] 等待验证码超时")
        return None


def init_skymail_client(config):
    """初始化 Skymail 客户端并生成 Token"""
    admin_email = config.get("skymail_admin_email", "")
    admin_password = config.get("skymail_admin_password", "")
    proxy = config.get("proxy", "")
    domains = config.get("skymail_domains", None)

    if not admin_email or not admin_password:
        print("[ERROR] 未配置 Skymail 管理员账号")
        print("   请在 config.json 中设置 skymail_admin_email 和 skymail_admin_password")
        sys.exit(1)

    if not domains or not isinstance(domains, list) or len(domains) == 0:
        print("[ERROR] 未配置 skymail_domains")
        print('   请在 config.json 中设置域名列表，例如: "skymail_domains": ["admin.example.com"]')
        sys.exit(1)

    client = SkymailClient(admin_email, admin_password, proxy=proxy, domains=domains)

    print(f"[INFO] 正在生成 Skymail API Token (API: {client.api_base})...")
    print(f"[INFO] 可用域名: {', '.join(domains)}")
    token = client.generate_token()

    if not token:
        print("[ERROR] Token 生成失败，无法继续")
        sys.exit(1)

    return client
