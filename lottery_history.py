import os
import json
import asyncio
import aiohttp
import pandas as pd
from datetime import datetime, timedelta
import pytz
import gspread
from google.oauth2 import service_account

# ==============================================================================
# ⚙️ 設定區
# ==============================================================================
tz = pytz.timezone("Asia/Taipei")

SPREADSHEET_NAME = "台灣彩券歷史數據_爬蟲結果"
API_BASE = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery"

GAME_CONFIG = {
    "威力彩": {"endpoint": "SuperLotto638Result", "key": "superLotto638Res", "type": "lotto"},
    "大樂透": {"endpoint": "Lotto649Result", "key": "lotto649Res", "type": "lotto"},
    "今彩539": {"endpoint": "Daily539Result", "key": "daily539Res", "type": "539"},
    "3星彩": {"endpoint": "3DResult", "key": "lotto3DRes", "type": "digit"},
    "4星彩": {"endpoint": "4DResult", "key": "lotto4DRes", "type": "digit"},
}

MONTHS_TO_FETCH = 24

# ==============================================================================
# 🔑 關鍵修改：從環境變數讀取金鑰
# ==============================================================================
def get_credentials():
    json_str = os.environ.get('GCP_SA_KEY')
    if not json_str:
        raise ValueError("⚠️ 找不到環境變數 GCP_SA_KEY，請檢查 GitHub Secrets 設定")
    
    key_dict = json.loads(json_str)
    creds = service_account.Credentials.from_service_account_info(
        key_dict, 
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    return creds

# ==============================================================================
# 🛠️ 核心邏輯
# ==============================================================================
def get_latest_periods_fast():
    creds = get_credentials()
    gc = gspread.authorize(creds)
    
    latest_db = {}
    try:
        try: sh = gc.open(SPREADSHEET_NAME)
        except: return {} # 如果檔案不存在，回傳空字典，稍後會建立

        for game_name in GAME_CONFIG.keys():
            try:
                ws = sh.worksheet(game_name)
                val = ws.acell('A2').value
                latest_db[game_name] = int(val) if val and val.isdigit() else 0
            except:
                latest_db[game_name] = 0
    except Exception as e:
        print(f"⚠️讀取舊資料時發生錯誤 (可能為首次執行): {e}")
        return {}
    return latest_db

def get_month_list(num_months):
    months = []
    current = datetime.now(tz)
    for _ in range(num_months):
        months.append(current.strftime("%Y-%m"))
        first = current.replace(day=1)
        current = first - timedelta(days=1)
    return months

def parse_game_data(item, game_type):
    try:
        period = int(item.get('period', 0))
        date_str = item.get('lotteryDate', '').split('T')[0]
        sell_amt = f"{item.get('sellAmount', 0):,}"
        
        jackpot_amt = "0"
        raw_total = item.get('totalAmount', 0)
        if raw_total > 0:
             jackpot_amt = f"{raw_total:,}"
        else:
             if 'jackpotAssign' in item:
                 jackpot_amt = f"{item['jackpotAssign'].get('prize', 0):,}"
             elif 'super638JackpotAssign' in item:
                 jackpot_amt = f"{item['super638JackpotAssign'].get('prize', 0):,}"
        
        row = {
            "期別": str(period),
            "開獎日期": date_str,
            "銷售金額": sell_amt,
            "本期總獎金 (含累積)": jackpot_amt
        }

        main_nums_list = []
        special_num_val = ""

        if game_type == "lotto":
            raw_nums = item.get('drawNumberSize', [])
            if raw_nums:
                main_nums_list = raw_nums[:-1]
                special_num_val = str(raw_nums[-1]).zfill(2)

        elif game_type == "539":
            raw_nums = item.get('drawNumberSize', [])
            if raw_nums: main_nums_list = raw_nums

        elif game_type == "digit":
            raw_nums = item.get('drawNumberAppear', [])
            if not raw_nums: raw_nums = item.get('drawNums', [])
            if raw_nums: main_nums_list = raw_nums

        for i, num in enumerate(main_nums_list):
            val = str(num).zfill(2) if game_type in ["lotto", "539"] else str(num)
            row[f"號碼{i+1}"] = val
            
        if special_num_val:
            row["特別號/第二區"] = special_num_val

        return row, period
    except:
        return None, 0

async def fetch_month_data(session, url, config, latest_period):
    try:
        async with session.get(url, timeout=10) as resp:
            if resp.status != 200: return []
            data = await resp.json()
            content = data.get('content', {}).get(config['key'])
            
            new_items = []
            if content:
                for item in content:
                    parsed_row, pid = parse_game_data(item, config['type'])
                    if parsed_row and pid > latest_period:
                        new_items.append(parsed_row)
            return new_items
    except:
        return []

async def fetch_all_games_async(existing_db):
    months = get_month_list(MONTHS_TO_FETCH)
    headers = {"User-Agent": "Mozilla/5.0"}
    
    new_data_results = {}
    
    async with aiohttp.ClientSession(headers=headers) as session:
        all_tasks = []
        for name, config in GAME_CONFIG.items():
            latest_period = existing_db.get(name, 0)
            for month in months:
                url = f"{API_BASE}/{config['endpoint']}?month={month}&pageNum=1&pageSize=60"
                # 建立 wrapper 來保留 game_name 資訊
                async def fetch_wrapper(n, u, c, l):
                    return n, await fetch_month_data(session, u, c, l)
                all_tasks.append(fetch_wrapper(name, url, config, latest_period))
        
        raw_results = await asyncio.gather(*all_tasks)
            
    for game_name, data_list in raw_results:
        if data_list:
            if game_name not in new_data_results:
                new_data_results[game_name] = []
            new_data_results[game_name].extend(data_list)

    final_results = {}
    for name, rows in new_data_results.items():
        if rows:
            df = pd.DataFrame(rows)
            # 去除重複期別
            df = df.drop_duplicates(subset=['期別'])
            final_results[name] = df
            
    return final_results

# ==============================================================================
# 🕸️ 抓取頭獎預估值 (行銷數字 API)
# ==============================================================================
def fetch_homepage_estimates():
    print("🕸️ 正在抓取官網【頭獎上看】行銷數字...")
    estimates = {}
    
    import requests
    import urllib3
    urllib3.disable_warnings()
    
    # ----------------------------------------------------------------------
    # 1. 嘗試抓取 'uptoPrize' API (行銷預估值)
    #    回傳範例: [{'gameCode': 5118, 'prize': '1.1', ...}, {'gameCode': 5134, 'prize': '5.2', ...}]
    #    5118 = 大樂透, 5134 = 威力彩
    # ----------------------------------------------------------------------
    try:
        url = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/uptoPrize"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        resp = requests.get(url, headers=headers, verify=False, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            items = data.get('content', {}).get('uptoPrizeList', [])
            
            for item in items:
                code = str(item.get('gameCode'))
                prize_str = str(item.get('prize', '0'))
                
                try:
                    # 轉換邏輯: "1.1" (億) -> 110000000
                    val = int(float(prize_str) * 100000000)
                    val_str = str(val)
                except:
                    val_str = "0"

                if code == "5134":
                    estimates["威力彩"] = val_str
                    print(f"   -> [API-上看] 威力彩: {prize_str} 億 ({val_str})")
                elif code == "5118":
                    estimates["大樂透"] = val_str
                    print(f"   -> [API-上看] 大樂透: {prize_str} 億 ({val_str})")
                
    except Exception as e:
        print(f"⚠️ 行銷數字 API 抓取失敗: {e}")

    # ----------------------------------------------------------------------
    # 2. 備援机制: Jackpot API (實際累積值)
    #    如果上面的 'uptoPrize' 沒抓到，改用 'Jackpot' 補救
    # ----------------------------------------------------------------------
    if "威力彩" not in estimates or "大樂透" not in estimates:
        try:
            print("   -> 部分資料缺失，啟動 [API-實際累積] 備援模式...")
            api_url = "https://api.taiwanlottery.com/TLCAPIWeB/Lottery/Jackpot"
            resp = requests.get(api_url, verify=False, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                jlist = data.get('content', {}).get('jackpotList', [])
                for item in jlist:
                    code = str(item.get('gameCode'))
                    amt = str(int(item.get('jackpot', 0))) # API 給的是整數
                    
                    if code == "5134" and "威力彩" not in estimates:
                        estimates["威力彩"] = amt
                        print(f"   -> [API-備援] 威力彩: {amt}")
                    elif code == "5118" and "大樂透" not in estimates:
                        estimates["大樂透"] = amt
                        print(f"   -> [API-備援] 大樂透: {amt}")
        except Exception as e:
            print(f"⚠️ API 備援失敗: {e}")

    return estimates

def save_to_gsheet(new_data_map, estimates={}):
    summary_report = []
    
    creds = get_credentials()
    gc = gspread.authorize(creds)
    
    try: sh = gc.open(SPREADSHEET_NAME)
    except: 
        print(f"⚠️ 找不到試算表，正在建立新檔案: {SPREADSHEET_NAME}")
        sh = gc.create(SPREADSHEET_NAME)
        try: sh.share("101angus@gmail.com", perm_type='user', role='writer')
        except: pass

    all_games = GAME_CONFIG.keys()
    
    for game_name in all_games:
        new_df = new_data_map.get(game_name)
        
        try:
            try:
                ws = sh.worksheet(game_name)
                old_data = ws.get_all_records()
                old_df = pd.DataFrame(old_data)
                if not old_df.empty:
                    old_df['期別'] = old_df['期別'].astype(str)
            except:
                ws = sh.add_worksheet(game_name, 100, 20)
                old_df = pd.DataFrame()
            
            final_df = old_df
            if new_df is not None and not new_df.empty:
                final_df = pd.concat([old_df, new_df], ignore_index=True)
            
            if not final_df.empty:
                if "期別" in final_df.columns:
                    final_df['期別'] = final_df['期別'].astype(str)
                    final_df = final_df.sort_values("期別", ascending=False)
                    final_df = final_df.drop_duplicates(subset=['期別'], keep='first')
                
                # [Fix] 絕對確保 "頭獎上看" 欄位存在
                if "頭獎上看" not in final_df.columns:
                    final_df["頭獎上看"] = ""
                    final_df["頭獎上看"] = final_df["頭獎上看"].fillna("")

                # 注入頭獎上看金額
                if game_name in estimates:
                    final_df = final_df.reset_index(drop=True)
                    final_df.loc[0, "頭獎上看"] = estimates[game_name]
                    summary_report.append(f"[{game_name}] 更新頭獎預估: {estimates[game_name]}")
            
            # 欄位排序 ("頭獎上看" 排在最後)
            desired_order = ["期別", "開獎日期", "號碼1", "號碼2", "號碼3", "號碼4", "號碼5", "號碼6", "特別號/第二區", "銷售金額", "本期總獎金 (含累積)", "頭獎上看"]
            final_cols = [c for c in desired_order if c in final_df.columns]
            final_cols += [c for c in final_df.columns if c not in final_cols]
            final_df = final_df.reindex(columns=final_cols).fillna("")
            
            # 強制轉型 string
            final_df = final_df.astype(str)

            ws.clear()
            ws.update([final_df.columns.values.tolist()] + final_df.values.tolist(), value_input_option='USER_ENTERED')
            
            if new_df is not None and not new_df.empty:
                summary_report.append(f"[{game_name}] 寫入 {len(new_df)} 筆新資料")
                
        except Exception as e:
            summary_report.append(f"[{game_name}] 處理失敗: {e}")

    print("-" * 30)
    for line in summary_report:
        print(line)
    print("-" * 30)
    print(f"📎 連結: {sh.url}")

# ==============================================================================
# 🚀 主程式執行
# ==============================================================================
async def main():
    print("🚀 開始執行歷史數據爬蟲...")
    
    # 1. 抓取首頁預估值
    estimates = fetch_homepage_estimates()
    
async def main():
    print(">>> 開始執行歷史數據爬蟲...")
    
    # 1. 抓取首頁預估值
    estimates = fetch_homepage_estimates()
    
    # Check Env var once
    has_creds = os.environ.get('GCP_SA_KEY') is not None
    
    if has_creds:
        # 2. 抓取歷史資料 (需連線 Google Sheets)
        try:
            latest_status = get_latest_periods_fast()
            new_res = await fetch_all_games_async(latest_status)
            
            # 3. 儲存並更新預估值
            save_to_gsheet(new_res, estimates)
        except Exception as e:
            print(f"[ERROR] 歷史資料抓取失敗: {e}")
    else:
        print("\n[注意: 本地測試模式] 未偵測到 GCP_SA_KEY")
        print(f"抓取到的頭獎預估值 (將寫入第12欄):")
        print(json.dumps(estimates, indent=4, ensure_ascii=False))
        print("----------")
        print(">>> 因無金鑰，已跳過歷史資料抓取與寫入測試。")
    
    print(">>> 執行完畢")

if __name__ == "__main__":
    # Fix for Windows console encoding
    import sys
    if sys.platform.startswith('win'):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    asyncio.run(main())
