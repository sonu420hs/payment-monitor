import json
import requests
import time
import re
import threading
import queue
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.checkbox import CheckBox
from kivy.uix.scrollview import ScrollView
from kivy.clock import Clock
from kivy.logger import Logger

# ========== HARDCODED (user se nahi lenge) ==========
API_TOKENS_SHEET_URL = "https://docs.google.com/spreadsheets/d/1WZGKmKXHjaOY78FzXiSMen7uUwxUc6c4PPLLTl8Sju4/export?format=csv&gid=0"

TELEGRAM_ACCOUNTS = [
    {
        'name': 'Primary Bot',
        'bot_token': '8605373478:AAECkjalIfIKKrbvWU9HxfLxduWc8bdVuVM',
        'chat_id': '7977729864',
        'enabled': True
    },
    {
        'name': 'Secondary Bot',
        'bot_token': '8642165948:AAFRds21Beu8hgYXFTYP9qTkqLL-RvkOxk4',
        'chat_id': '5805627493',
        'enabled': True
    }
]
# ===================================================

# Global queue for logs (UI update ke liye)
log_queue = queue.Queue()

class GoogleSheetLoader:
    def __init__(self, csv_url: str):
        self.csv_url = csv_url
    def fetch_data(self) -> Optional[List[Dict]]:
        try:
            response = requests.get(self.csv_url, timeout=30)
            response.raise_for_status()
            lines = response.text.strip().split('\n')
            if not lines: return None
            headers = self._parse_csv_line(lines[0])
            records = []
            for line in lines[1:]:
                if line.strip():
                    values = self._parse_csv_line(line)
                    if len(values) == len(headers):
                        record = {headers[i].strip(): values[i].strip() for i in range(len(headers))}
                        records.append(record)
            return records
        except Exception as e:
            return None
    def _parse_csv_line(self, line: str) -> List[str]:
        result, current, in_quotes = [], "", False
        for char in line:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ',' and not in_quotes:
                result.append(current.strip())
                current = ""
            else:
                current += char
        result.append(current.strip())
        return result

class UniversalPaymentMonitor:
    def __init__(self, bank_sheet_url: str, global_min_amount: int = 3000, max_amount: int = None):
        self.bank_sheet_url = bank_sheet_url
        self.bank_accounts = {}
        self.api_tokens = {}
        self.matches_log = {}
        self.global_min_amount = global_min_amount
        self.global_max_amount = max_amount   # None means no upper limit
        self.apps = {}
        self.running = False
        self.cycle_thread = None
        self.load_api_tokens_from_sheet()  # Hardcoded sheet
        self.load_bank_accounts_from_sheet()
        self.initialize_apps()

    def load_bank_accounts_from_sheet(self):
        loader = GoogleSheetLoader(self.bank_sheet_url)
        records = loader.fetch_data()
        if not records:
            self._add_log("❌ Bank accounts sheet load fail")
            return
        self.bank_accounts.clear()
        for row in records:
            account_number = None
            for key in ['account_number', 'account_no', 'acctNo', 'Account Number', 'ACCOUNT_NUMBER', 'Account No', 'Account']:
                if key in row and row[key]:
                    account_number = str(row[key]).strip()
                    break
            ifsc_code = None
            for key in ['ifsc_code', 'IFSC Code', 'ifsc', 'IFSC', 'bank_ifsc']:
                if key in row and row[key]:
                    ifsc_code = str(row[key]).strip()
                    break
            if not account_number: continue
            last_4 = self.extract_last_4_digits(account_number)
            first_4_ifsc = self.extract_first_4_ifsc(ifsc_code) if ifsc_code else None
            if last_4:
                key = f"{last_4}|{first_4_ifsc}" if first_4_ifsc else last_4
                if key not in self.bank_accounts:
                    self.bank_accounts[key] = []
                account_holder = 'N/A'
                for name_key in ['account_holder', 'Account Holder', 'holder_name', 'name', 'customer_name', 'Name']:
                    if name_key in row and row[name_key]:
                        account_holder = str(row[name_key]).strip()
                        break
                bank_name = 'N/A'
                for bank_key in ['bank_name', 'Bank Name', 'bank', 'Bank']:
                    if bank_key in row and row[bank_key]:
                        bank_name = str(row[bank_key]).strip()
                        break
                self.bank_accounts[key].append({
                    'full_account': account_number,
                    'account_holder': account_holder,
                    'bank_name': bank_name,
                    'ifsc_code': ifsc_code or 'N/A',
                    'first_4_ifsc': first_4_ifsc
                })
        self._add_log(f"✅ Bank accounts loaded: {len(self.bank_accounts)} patterns")

    def load_api_tokens_from_sheet(self):
        loader = GoogleSheetLoader(API_TOKENS_SHEET_URL)
        records = loader.fetch_data()
        if not records:
            self._add_log("❌ API tokens sheet load fail")
            return
        app_name_col, token_col = None, None
        if records:
            headers = list(records[0].keys())
            for h in headers:
                if 'app' in h.lower() or 'name' in h.lower():
                    app_name_col = h
                if 'token' in h.lower():
                    token_col = h
        if not app_name_col or not token_col:
            self._add_log("❌ Columns 'APP NAME' or 'TOKEN NO' missing")
            return
        self.api_tokens = {}
        for row in records:
            app_name = row.get(app_name_col, '').strip()
            token = row.get(token_col, '').strip()
            if app_name and token:
                self.api_tokens[app_name] = token
        self._add_log(f"✅ API tokens loaded: {len(self.api_tokens)}")

    def initialize_apps(self):
        app_configs = {
            'FloxyPay': {'api_url': 'https://api.plavix.skin/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://web.floxypay.ink', 'referer': 'https://web.floxypay.ink/', 'min_amount': 100, 'method': 1},
            'TiveraPay': {'api_url': 'https://r6w1t4doia.com/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://tivrapay5.com', 'referer': 'https://tivrapay5.com/', 'min_amount': 100, 'method': 1},
            'GMPay': {'api_url': 'https://api.gmpay.wiki/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://web.gmpay.top', 'referer': 'https://web.gmpay.top/', 'min_amount': 100, 'method': 1},
            'Ignipay': {'api_url': 'https://api.igni.ink/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://refer.iplp2p.top', 'referer': 'https://refer.iplp2p.top/', 'min_amount': 100, 'method': 1},
            'MilesPay': {'api_url': 'https://api.gronix.xyz/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://milesm.skin', 'referer': 'https://milesm.skin/', 'min_amount': 100, 'method': 0},
            'SixPay': {'api_url': 'https://api.sixpay88.com/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://web.sixpay888.com', 'referer': 'https://web.sixpay888.com/', 'min_amount': 100, 'method': 1},
            'SuperCoinPay': {'api_url': 'https://rapi.supercoinpay.com/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://refer.supercoinpay.com', 'referer': 'https://refer.supercoinpay.com/', 'min_amount': 100, 'method': 1},
            'ViviPay': {'api_url': 'https://qonix.click/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://vivipay3.com', 'referer': 'https://vivipay3.com/', 'min_amount': 100, 'method': 1},
            'Zippay': {'api_url': 'https://api.kelura.xyz/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://web.zippay.wiki', 'referer': 'https://web.zippay.wiki/', 'min_amount': 100, 'method': 1},
            'UnoTask': {'api_url': 'https://ddddtaskapp.com/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://web.uonotask9.com', 'referer': 'https://web.uonotask9.com/', 'min_amount': 100, 'method': 1},
            'RichPay': {'api_url': 'https://mgr.inpays.top/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://store.richpay.info', 'referer': 'https://store.richpay.info/', 'min_amount': 100, 'method': 1},
            'Gtod': {'api_url': 'https://api.crelyn.xyz/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://gtod.top', 'referer': 'https://gtod.top/', 'min_amount': 100, 'method': 1},
            'XWalletPay': {'api_url': 'https://xwallethelp.com/xxapi/buyitoken/waitpayerpaymentslip', 'origin': 'https://xwalletsapp.com', 'referer': 'https://xwalletsapp.com/', 'min_amount': 100, 'method': 1}
        }
        self.apps.clear()
        for name, cfg in app_configs.items():
            token = self.api_tokens.get(name)
            if not token:
                self._add_log(f"⚠️ {name}: No token -> DISABLED")
                continue
            self.apps[name] = {
                'api_url': cfg['api_url'],
                'headers': {'accept': 'application/json, text/plain, */*', 'accept-language': 'en-us', 'indiatoken': token, 'origin': cfg['origin'], 'referer': cfg['referer'], 'user-agent': 'Mozilla/5.0'},
                'min_amount': cfg['min_amount'],
                'method': cfg['method'],
                'enabled': True,
                'token_expired': False,
                'last_error': None,
                'success_count': 0,
                'fail_count': 0,
                'expiry_alert_sent': False
            }
        self._add_log(f"📱 Apps initialised: {len(self.apps)} active")

    def send_telegram_notification(self, title: str, message: str, app_name: str = "Universal"):
        full_message = f"🔔 *{title}* 🔔\n\n📱 *App:* {app_name}\n{message}\n\n━━━━━━━━━━━━━━━━\n⏰ {datetime.now().strftime('%H:%M:%S')}\n🔄 Universal Monitor"
        for bot in TELEGRAM_ACCOUNTS:
            if not bot.get('enabled', True): continue
            bot_token = bot.get('bot_token')
            chat_id = bot.get('chat_id')
            if not bot_token or not chat_id: continue
            try:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                requests.post(url, json={"chat_id": chat_id, "text": full_message, "parse_mode": "Markdown"}, timeout=10)
            except Exception:
                pass

    @staticmethod
    def extract_last_4_digits(account_number: str) -> Optional[str]:
        if not account_number: return None
        account_str = re.sub(r'\D', '', str(account_number))
        return account_str[-4:] if len(account_str) >= 4 else account_str if account_str else None

    @staticmethod
    def extract_first_4_ifsc(ifsc_code: str) -> Optional[str]:
        if not ifsc_code: return None
        ifsc_str = re.sub(r'\s+', '', str(ifsc_code).upper())
        return ifsc_str[:4] if len(ifsc_str) >= 4 else ifsc_str if ifsc_str else None

    def fetch_api_data(self, app_name: str, app_config: Dict, page: int = 1, limit: int = 60) -> Optional[Dict]:
        min_amount = max(app_config.get('min_amount', 100), self.global_min_amount)
        params = {'page': page, 'limit': limit, 'if_asc': 'false', 'min_amount': min_amount, 'max_amount': self.global_max_amount or 100000, 'method': app_config.get('method', 1), 'date_asc': 0}
        try:
            response = requests.get(app_config['api_url'], headers=app_config['headers'], params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if data.get('code') == 0:
                if app_config.get('token_expired'):
                    app_config['token_expired'] = False
                return data
            else:
                app_config['token_expired'] = True
                app_config['last_error'] = f"Code: {data.get('code')}, Msg: {data.get('msg')}"
                return None
        except Exception as e:
            app_config['fail_count'] += 1
            if "401" in str(e) or "403" in str(e):
                app_config['token_expired'] = True
                app_config['last_error'] = str(e)
            return None

    def get_all_transactions(self, app_name: str, app_config: Dict, max_pages: int = 60) -> List[Dict]:
        all_trans = []
        page = 1
        while page <= max_pages:
            data = self.fetch_api_data(app_name, app_config, page=page)
            if not data or data.get('code') != 0:
                break
            transactions = data.get('data', {}).get('list', [])
            total = data.get('data', {}).get('total', 0)
            for t in transactions:
                if t.get('amount', 0) >= self.global_min_amount:
                    if self.global_max_amount is None or t.get('amount', 0) <= self.global_max_amount:
                        all_trans.append(t)
            if len(transactions) >= total:
                break
            page += 1
        return all_trans

    def check_match(self, account_number: str, ifsc_code: str) -> bool:
        if not self.bank_accounts: return False
        last_4 = self.extract_last_4_digits(account_number)
        ifsc_first4 = self.extract_first_4_ifsc(ifsc_code)
        if last_4:
            key = f"{last_4}|{ifsc_first4}" if ifsc_first4 else last_4
            return key in self.bank_accounts
        return False

    def get_bank_match_details(self, account_number: str, ifsc_code: str) -> Optional[List[Dict]]:
        last_4 = self.extract_last_4_digits(account_number)
        ifsc_first4 = self.extract_first_4_ifsc(ifsc_code)
        if last_4:
            key = f"{last_4}|{ifsc_first4}" if ifsc_first4 else last_4
            return self.bank_accounts.get(key)
        return None

    def process_app(self, app_name: str, app_config: Dict) -> Tuple[int, int]:
        if app_config.get('token_expired'):
            return 0, 0
        transactions = self.get_all_transactions(app_name, app_config)
        if not transactions:
            return 0, 0
        matches_found = 0
        for t in transactions:
            acc = t.get('acctNo', '')
            ifsc = t.get('acctCode', '')
            if self.check_match(acc, ifsc):
                rpt_no = t.get('rptNo', '')
                mid = f"{app_name}_{rpt_no}"
                if mid not in self.matches_log:
                    matches_found += 1
                    self.matches_log[mid] = datetime.now().isoformat()
                    bank_details = self.get_bank_match_details(acc, ifsc)
                    self._add_log(f"✅ MATCH {app_name} | ₹{t.get('amount')} | {t.get('acctName')}")
                    # Telegram alert
                    api_name = t.get('acctName', 'N/A')
                    api_amount = t.get('amount', 0)
                    last4 = self.extract_last_4_digits(acc)
                    ifsc4 = self.extract_first_4_ifsc(ifsc)
                    bank_holder = bank_details[0]['account_holder'] if bank_details else 'Unknown'
                    bank_name = bank_details[0]['bank_name'] if bank_details else 'Unknown'
                    rpt_last4 = rpt_no[-4:] if rpt_no != 'N/A' and len(str(rpt_no)) >= 4 else rpt_no
                    msg = f"🔥 PAYMENT MATCH 🔥\n👤 API: {api_name}\n🏦 Account: ****{last4}\n💰 ₹{api_amount}\n📋 Report No: {rpt_last4}\n📁 Sheet: {bank_holder} | {bank_name}"
                    self.send_telegram_notification(f"₹{api_amount} - {app_name}", msg, app_name)
                    app_config['success_count'] += 1
        return matches_found, len(transactions)

    def _add_log(self, text):
        log_queue.put(f"{datetime.now().strftime('%H:%M:%S')} - {text}")

    def run_cycle(self):
        if not self.running:
            return
        self._add_log("🔄 Refreshing sheets & starting cycle...")
        self.load_bank_accounts_from_sheet()
        self.load_api_tokens_from_sheet()
        self.initialize_apps()
        total_matches = 0
        for name, cfg in self.apps.items():
            if not cfg.get('enabled', True):
                continue
            matches, checked = self.process_app(name, cfg)
            total_matches += matches
            time.sleep(2)
        self._add_log(f"✅ Cycle done. Matches: {total_matches}")
        # Schedule next cycle only if still running
        if self.running:
            Clock.schedule_once(lambda dt: self.run_cycle(), 60)  # 60 sec interval

    def start(self):
        if self.running:
            return
        self.running = True
        self._add_log("🚀 Monitoring STARTED")
        Clock.schedule_once(lambda dt: self.run_cycle(), 0)

    def stop(self):
        self.running = False
        self._add_log("🛑 Monitoring STOPPED")

# ========== KIVY GUI ==========
class MonitorApp(App):
    def build(self):
        self.monitor = None
        main_layout = BoxLayout(orientation='vertical', padding=10, spacing=10)

        # Google Sheet URL input
        url_layout = BoxLayout(orientation='horizontal', size_hint_y=0.08)
        url_layout.add_widget(Label(text="Bank Sheet URL:", size_hint_x=0.3))
        self.url_input = TextInput(text="https://docs.google.com/spreadsheets/d/1_16sHMUIJO5KPPEA5-uwphFPl-QBxvUAQoaNHX0MnfE/export?format=csv&gid=0", size_hint_x=0.7)
        url_layout.add_widget(self.url_input)
        main_layout.add_widget(url_layout)

        # Filters
        filter_layout = GridLayout(cols=4, size_hint_y=0.1, spacing=5)
        filter_layout.add_widget(Label(text="Min Amount:"))
        self.min_amount_input = TextInput(text="3000", input_filter='int')
        filter_layout.add_widget(self.min_amount_input)
        filter_layout.add_widget(Label(text="Max Amount (0 = no limit):"))
        self.max_amount_input = TextInput(text="0", input_filter='int')
        filter_layout.add_widget(self.max_amount_input)
        main_layout.add_widget(filter_layout)

        # Buttons
        btn_layout = BoxLayout(orientation='horizontal', size_hint_y=0.1, spacing=10)
        self.start_btn = Button(text="START", background_color=(0,1,0,1))
        self.start_btn.bind(on_press=self.start_monitor)
        self.stop_btn = Button(text="STOP", background_color=(1,0,0,1))
        self.stop_btn.bind(on_press=self.stop_monitor)
        btn_layout.add_widget(self.start_btn)
        btn_layout.add_widget(self.stop_btn)
        main_layout.add_widget(btn_layout)

        # Live log
        log_label = Label(text="Live Log:", size_hint_y=0.05, halign='left')
        main_layout.add_widget(log_label)
        self.log_scroll = ScrollView(size_hint_y=0.6)
        self.log_text = Label(text="", size_hint_y=None, markup=True, valign='top', halign='left')
        self.log_text.bind(size=self.log_text.setter('text_size'))
        self.log_scroll.add_widget(self.log_text)
        main_layout.add_widget(self.log_scroll)

        # Start updating logs from queue
        Clock.schedule_interval(self.update_logs, 0.5)
        return main_layout

    def start_monitor(self, instance):
        if self.monitor and self.monitor.running:
            return
        sheet_url = self.url_input.text.strip()
        min_amt = int(self.min_amount_input.text or "3000")
        max_amt_text = self.max_amount_input.text.strip()
        max_amt = None if max_amt_text == "0" else int(max_amt_text)
        self.monitor = UniversalPaymentMonitor(bank_sheet_url=sheet_url, global_min_amount=min_amt, max_amount=max_amt)
        self.monitor.start()

    def stop_monitor(self, instance):
        if self.monitor:
            self.monitor.stop()

    def update_logs(self, dt):
        lines = []
        while not log_queue.empty():
            lines.append(log_queue.get())
        if lines:
            current = self.log_text.text
            new_text = current + "\n" + "\n".join(lines)
            # Keep only last 1000 lines to avoid lag
            lines_split = new_text.splitlines()
            if len(lines_split) > 1000:
                new_text = "\n".join(lines_split[-1000:])
            self.log_text.text = new_text
            # Auto-scroll to bottom
            self.log_scroll.scroll_y = 0

if __name__ == "__main__":
    MonitorApp().run()