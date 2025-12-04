import os
import json
import asyncio
import aiohttp
import re
import pandas as pd
import pytz
from datetime import datetime
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
import gspread
from google.oauth2 import service_account

# ==============================================================================
# ⚙️ 設定區
# ==============================================================================
tz = pytz.timezone("Asia/Taipei")
report_time = datetime.now(tz).strftime("%Y/%m/%d %H:%M")

SPREADSHEET_NAME = "刮刮樂銷售資訊_自動抓取"
SHEET_TAB_NAME = "刮刮樂銷售資訊"
TAIWAN_LOTTERY_URL = "https://www.taiwanlottery.com/instant/sale/"
API_BASE_URL = "https://api.taiwanlottery.com/TLCAPIWeB/News/Detail/"

# ==============================================================================
# 🔑 關鍵修改：從環境變數讀取金鑰 (不要用檔案路徑)
# ==============================================================================
def get_credentials():
    # 在 GitHub Secrets 中設定 GCP_SA_KEY
    json_str = os.environ.get('GCP_SA_KEY')
    if not json_str:
        raise ValueError("⚠️ 找不到環境變數 GCP_SA_KEY，請檢查 GitHub Secrets 設定")
    
    key_dict = json.loads(json_str)
    creds = service_account.Credentials.from_service_account_info(
        key_dict, 
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    return creds

# ... (中間的 fetch_prize_via_api_async 和 scrape_all_scratchcards 函式邏輯保持不變) ...
# 注意：請把原本 code 裡的 !pip 和 drive.mount 都刪除

# ==============================================================================
# 💾 資料儲存：寫入 Google Sheets (修改驗證部分)
# ==============================================================================
def save_to_google_sheet(df):
    if df.empty:
        print("⚠️ 無資料可寫入。")
        return

    try:
        print("💾 正在寫入 Google Sheet...")
        df = df.fillna("")
        
        # 使用新的驗證函式
        creds = get_credentials()
        gc = gspread.authorize(creds)
        
        try: sh = gc.open(SPREADSHEET_NAME)
        except: sh = gc.create(SPREADSHEET_NAME); sh.share("101angus@gmail.com", perm_type='user', role='writer')
        
        try: ws = sh.worksheet(SHEET_TAB_NAME); ws.clear()
        except: ws = sh.add_worksheet(SHEET_TAB_NAME, 1, 1)
            
        fixed_cols = ["編號", "名稱", "售價", "最高獎金", "發行日", "下市日", "兌獎截止日", "銷售率", "頭獎張數", "頭獎未兌領"]
        link_col = ["獎金結構連結"]
        
        all_cols = df.columns.tolist()
        dynamic_cols = [c for c in all_cols if c not in fixed_cols and c not in link_col]
        
        stats_cols = []
        money_cols = []
        
        # 需要把原本的 money_sorter 函式也放進來或移到全域
        def money_sorter(money_str):
            try:
                clean = re.sub(r'[^\d]', '', str(money_str))
                return int(clean) if clean else -1
            except:
                return -1

        for c in dynamic_cols:
            if "發行" in c or "中獎" in c:
                stats_cols.append(c)
            else:
                money_cols.append(c)
        
        def sort_stats(col_name):
            if "發行" in col_name: return 0
            if "中獎" in col_name: return 1
            return 2
        
        stats_cols.sort(key=sort_stats)
        money_cols.sort(key=money_sorter, reverse=True)
        
        final_cols = fixed_cols + stats_cols + money_cols + link_col
        df_final = df.reindex(columns=final_cols, fill_value="")
        
        ws.update([df_final.columns.values.tolist()] + df_final.values.tolist(), value_input_option='USER_ENTERED')
        print(f"🎉 成功寫入 {len(df)} 筆資料！")
        
    except Exception as e:
        print(f"⚠️ 存檔失敗: {e}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    df_result = loop.run_until_complete(scrape_all_scratchcards())
    save_to_google_sheet(df_result)