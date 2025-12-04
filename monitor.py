import os
import json
import requests
from bs4 import BeautifulSoup
import pandas as pd
from datetime import datetime
import time
import re
import pytz
import concurrent.futures
from urllib.parse import quote_plus, urlencode
from collections import Counter
import gspread
from google.oauth2 import service_account

# ==============================================================================
# ⚙️ 設定區
# ==============================================================================
tz = pytz.timezone("Asia/Taipei")
now = datetime.now(tz)
report_time = now.strftime("%Y/%m/%d %H:%M")

SPREADSHEET_NAME = "全台投注站監控報告_自動抓取"
SHARING_EMAIL = "101angus@gmail.com" # 你的 Email
PREV_SPORTS_SHEET_NAME = "前次運彩"
PREV_LOTTERY_SHEET_NAME = "前次台彩"
DISAPPEARED_LOG_SHEET_NAME = "Disappeared_Log"

MAPS_SEARCH_URL = "https://www.google.com/maps/search/"

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
# 🗺️ 地理資訊與常數
# ==============================================================================
sports_base_url = "https://blob.sportslottery.com.tw/static/map2/iframe/"
sports_city_map = {
    "TaipeiCity": "台北市", "NewTaipeiCity": "新北市", "KeelungCity": "基隆市",
    "TaoyuanCounty": "桃園市", "HsinchuCity": "新竹市", "HsinchuCounty": "新竹縣",
    "MiaoliCounty": "苗栗縣", "TaichungCity": "台中市", "ChanghuaCounty": "彰化縣",
    "NantouCounty": "南投縣", "YunlinCounty": "雲林縣", "ChiayiCity": "嘉義市",
    "ChiayiCounty": "嘉義縣", "TainanCity": "台南市", "KaohsiungCity": "高雄市",
    "PingtungCounty": "屏東縣", "YilanCounty": "宜蘭縣", "HualienCounty": "花蓮縣",
    "TaitungCounty": "台東縣", "PenghuCounty": "澎湖縣", "KinmenCounty": "金門縣",
    "LienchiangCounty": "連江縣"
}

county_city_map = {
    "臺北市": ["中正區","大同區","中山區","松山區","大安區","萬華區","信義區","士林區","北投區","內湖區","南港區","文山區"],
    "新北市": ["板橋區","新莊區","中和區","永和區","土城區","樹林區","三峽區","鶯歌區","三重區","蘆洲區","五股區","泰山區","林口區","八里區","淡水區","三芝區","石門區","金山區","萬里區","汐止區","瑞芳區","貢寮區","雙溪區","平溪區","新店區","深坑區","石碇區","坪林區","烏來區"],
    "桃園市": ["桃園區","中壢區","平鎮區","八德區","楊梅區","蘆竹區","大溪區","龍潭區","龜山區","大園區","觀音區","新屋區","復興區"],
    "臺中市": ["中區","東區","南區","西區","北區","北屯區","西屯區","南屯區","太平區","大里區","霧峰區","烏日區","豐原區","后里區","石岡區","東勢區","和平區","新社區","潭子區","大雅區","神岡區","大肚區","沙鹿區","龍井區","梧棲區","清水區","大甲區","外埔區","大安區"],
    "臺南市": ["中西區","東區","南區","北區","安平區","安南區","永康區","歸仁區","新化區","左鎮區","玉井區","楠西區","南化區","仁德區","關廟區","龍崎區","官田區","麻豆區","佳里區","西港區","七股區","將軍區","學甲區","北門區","新營區","後壁區","白河區","東山區","六甲區","下營區","柳營區","鹽水區","善化區","大內區","山上區","新市區","安定區"],
    "高雄市": ["新興區","前金區","苓雅區","鹽埕區","鼓山區","旗津區","前鎮區","三民區","楠梓區","小港區","左營區","仁武區","大社區","岡山區","路竹區","阿蓮區","田寮區","燕巢區","橋頭區","梓官區","彌陀區","永安區","湖內區","鳳山區","大寮區","林園區","鳥松區","大樹區","旗山區","美濃區","六龜區","甲仙區","杉林區","內門區","茂林區","桃源區","那瑪夏區","茄萣區"],
    "基隆市": ["仁愛區","信義區","中正區","中山區","安樂區","暖暖區","七堵區"],
    "新竹市": ["東區","北區","香山區"],
    "新竹縣": ["竹北市","竹東鎮","新埔鎮","關西鎮","湖口鄉","新豐鄉","芎林鄉","橫山鄉","北埔鄉","寶山鄉","峨眉鄉","尖石鄉","五峰鄉"],
    "苗栗縣": ["苗栗市","頭份市","苑裡鎮","通霄鎮","竹南鎮","後龍鎮","卓蘭鎮","西湖鄉","頭屋鄉","公館鄉","銅鑼鄉","三義鄉","造橋鄉","三灣鄉","南庄鄉","大湖鄉","獅潭鄉","泰安鄉"],
    "彰化縣": ["彰化市","員林市","和美鎮","鹿港鎮","溪湖鎮","二林鎮","田中鎮","北斗鎮","花壇鄉","芬園鄉","大村鄉","永靖鄉","伸港鄉","線西鄉","福興鄉","秀水鄉","埔心鄉","埔鹽鄉","大城鄉","芳苑鄉","竹塘鄉","社頭鄉","二水鄉","田尾鄉","埤頭鄉","溪州鄉"],
    "南投縣": ["南投市","草屯鎮","竹山鎮","集集鎮","名間鄉","鹿谷鄉","中寮鄉","魚池鄉","國姓鄉","水里鄉","信義鄉","仁愛鄉","埔里鎮"],
    "雲林縣": ["斗六市","斗南鎮","虎尾鎮","西螺鎮","土庫鎮","北港鎮","古坑鄉","大埤鄉","莿桐鄉","林內鄉","二崙鄉","崙背鄉","麥寮鄉","東勢鄉","褒忠鄉","臺西鄉","元長鄉","四湖鄉","口湖鄉","水林鄉"],
    "嘉義市": ["東區","西區"],
    "嘉義縣": ["太保市","朴子市","布袋鎮","大林鎮","民雄鄉","溪口鄉","新港鄉","六腳鄉","東石鄉","義竹鄉","鹿草鄉","水上鄉","中埔鄉","竹崎鄉","梅山鄉","番路鄉","大埔鄉","阿里山鄉"],
    "宜蘭縣": ["宜蘭市","羅東鎮","蘇澳鎮","頭城鎮","礁溪鄉","壯圍鄉","員山鄉","冬山鄉","五結鄉","三星鄉","大同鄉","南澳鄉"],
    "花蓮縣": ["花蓮市","鳳林鎮","玉里鎮","新城鄉","吉安鄉","壽豐鄉","光復鄉","豐濱鄉","瑞穗鄉","富里鄉","秀林鄉","萬榮鄉","卓溪鄉"],
    "臺東縣": ["臺東市","成功鎮","關山鎮","卑南鄉","鹿野鄉","池上鄉","東河鄉","長濱鄉","太麻里鄉","大武鄉","綠島鄉","延平鄉","海端鄉","達仁鄉","金峰鄉","蘭嶼鄉"],
    "澎湖縣": ["馬公市","湖西鄉","白沙鄉","西嶼鄉","望安鄉","七美鄉"],
    "金門縣": ["金城鎮","金湖鎮","金沙鎮","金寧鄉","烈嶼鄉","烏坵鄉"],
    "連江縣": ["南竿鄉","北竿鄉","莒光鄉","東引鄉"],
    "屏東縣": ["屏東市","潮州鎮","東港鎮","恆春鎮","萬丹鄉","長治鄉","麟洛鄉","九如鄉","里港鄉","鹽埔鄉","高樹鄉","萬巒鄉","內埔鄉","竹田鄉","新埤鄉","枋寮鄉","新園鄉","崁頂鄉","林邊鄉","南州鄉","佳冬鄉","琉球鄉","車城鄉","滿州鄉","枋山鄉","三地門鄉","霧臺鄉","瑪家鄉","泰武鄉","來義鄉","春日鄉","獅子鄉","牡丹鄉"]
}

# --- 行政區解析 ---
def extract_district(county_name, address):
    county_name = county_name.replace("台", "臺")
    address_normalized = address.replace("台", "臺")
    districts = []
    target_county_key = None
    for k in county_city_map.keys():
        if k.replace("臺", "台") == county_name.replace("臺", "台"):
            target_county_key = k
            break
        if k == county_name:
             target_county_key = k
             break

    if target_county_key:
        districts = county_city_map[target_county_key]
    if not address or not districts:
        return ""
    districts.sort(key=len, reverse=True)
    for district in districts:
        if district in address_normalized:
             return district
    return ""

# --- 標準化 ---
def normalize(text):
    if pd.isna(text):
        return ""
    text = str(text).strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("台", "臺")
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("Ａ", "A").replace("Ｂ", "B").replace("Ｃ", "C")
    return str(text).lower()

# --- 核心比對函式 ---
def mark_status(df_now, df_prev, key_col="投注站名稱", addr_col="地址"):
    now = df_now.copy()
    prev = df_prev.copy()
    missing_cols = list(set(now.columns) - set(prev.columns))
    for col in missing_cols: prev[col] = ''
    if addr_col not in prev.columns: prev[addr_col] = ""

    for df in [now, prev]:
         for col in [key_col, addr_col, "縣市"]:
            if col in df.columns: df[col] = df[col].astype(str).str.strip()

    for df in [now, prev]:
        df['norm_key'] = df[key_col].apply(normalize)
        df['norm_addr'] = df[addr_col].apply(normalize)
        df['norm_city'] = df["縣市"].apply(normalize)

    now["full_key"] = now['norm_key'] + "_" + now['norm_addr']
    prev["full_key"] = prev['norm_key'] + "_" + prev['norm_addr']

    prev_keys = set(prev["full_key"])
    status_list = []
    original_addr_list = []
    moved_keys = set()

    for idx, row in now.iterrows():
        full_key = row["full_key"]
        norm_key = row["norm_key"]
        current_city = row["norm_city"]

        if full_key in prev_keys:
            status_list.append("沒變")
            original_addr_list.append("")
        else:
            match_name_city = prev[(prev['norm_key'] == norm_key) & (prev['norm_city'] == current_city)]
            if not match_name_city.empty:
                status_list.append("搬家")
                original_addr_list.append(match_name_city[addr_col].iloc[0])
                moved_keys.add(norm_key)
            else:
                status_list.append("新設")
                original_addr_list.append("")
                moved_keys.add(norm_key)

    now["狀態"] = status_list
    now["原地址"] = original_addr_list

    # 處理消失資料
    now_keys = set(now["full_key"])
    disappeared_keys = prev_keys - now_keys
    disappeared_rows = prev[prev["full_key"].isin(disappeared_keys)].copy()
    disappeared_rows = disappeared_rows[~disappeared_rows['norm_key'].isin(moved_keys)]

    disappeared_rows["狀態"] = "消失"
    disappeared_rows["原地址"] = disappeared_rows[addr_col]
    disappeared_rows[addr_col] = ""

    disappeared_rows = disappeared_rows[[col for col in now.columns if col in disappeared_rows.columns]]
    final_df = pd.concat([now, disappeared_rows], ignore_index=True)
    final_df.drop(columns=["full_key", 'norm_key', 'norm_addr', 'norm_city'], inplace=True)

    return final_df

# --- 報告產生函式 ---
def generate_report(df, source_name, timestamp):
    status_counts = Counter(df["狀態"])
    summary_states = ["沒變", "新設", "搬家", "消失", "恢復營業"]
    summary = pd.DataFrame({
        "抓取時間": [timestamp] * len(summary_states),
        "來源": [source_name] * len(summary_states),
        "狀態": summary_states,
        "筆數": [status_counts.get(s, 0) for s in summary_states]
    })

    changes = df[df["狀態"].isin(["新設", "搬家", "消失", "恢復營業"])].copy()
    required_cols = ["投注站名稱", "縣市", "地址", "原地址", "行政區"]
    for col in required_cols:
        if col not in changes.columns: changes[col] = ''

    def generate_hyperlink_formula(search_name, search_address, link_text):
        if not search_address or str(search_address).strip() == "" or str(search_address).startswith("=HYPERLINK"):
            return ""
        search_query = f"{search_name} {search_address}"
        return f'=HYPERLINK("{MAPS_SEARCH_URL}{quote_plus(search_query)}", "{link_text}")'

    changes['Google 地圖連結 (現址)'] = changes.apply(
        lambda row: generate_hyperlink_formula(row["投注站名稱"], row["地址"], "現址地圖"), axis=1
    )
    changes['Google 地圖連結 (原址)'] = changes.apply(
        lambda row: generate_hyperlink_formula(row["投注站名稱"], row["原地址"], "原址地圖"), axis=1
    )

    changes.insert(0, "抓取時間", timestamp)
    changes.insert(1, "來源", source_name)

    final_order = [
        "抓取時間", "來源", "狀態", "投注站名稱",
        "縣市", "行政區", "地址", "Google 地圖連結 (現址)",
        "原地址", "Google 地圖連結 (原址)"
    ]
    changes = changes[[col for col in final_order if col in changes.columns]]
    return summary, changes

# --- 消失紀錄函式 ---
def update_disappeared_log(sh, df_changes, source_name, timestamp):
    if sh is None: return 0
    disappeared_data = df_changes[df_changes['狀態'] == '消失'].copy()
    if disappeared_data.empty: return 0

    disappeared_data['消失日期'] = timestamp
    disappeared_data['地址'] = disappeared_data['原地址'] 
    if "來源" not in disappeared_data.columns: disappeared_data.insert(0, "來源", source_name)

    disappeared_cols = ["來源", "投注站名稱", "地址", "縣市", "行政區", "消失日期"]
    disappeared_data = disappeared_data[[col for col in disappeared_cols if col in disappeared_data.columns]].fillna('')

    try:
        ws_log = sh.worksheet(DISAPPEARED_LOG_SHEET_NAME)
        existing_header = ws_log.row_values(1)
        if existing_header != disappeared_cols:
             ws_log.clear()
             ws_log.update([disappeared_cols], 'A1')
    except gspread.exceptions.WorksheetNotFound:
        ws_log = sh.add_worksheet(title=DISAPPEARED_LOG_SHEET_NAME, rows=1, cols=len(disappeared_cols))
        ws_log.update([disappeared_cols], 'A1')

    ws_log.append_rows(disappeared_data.values.tolist(), value_input_option='USER_ENTERED')
    print(f"✅ 已追加 {len(disappeared_data)} 筆消失資料到「{DISAPPEARED_LOG_SHEET_NAME}」工作表。")
    return len(disappeared_data)

# ==============================================================================
# 🚀 資料抓取函式
# ==============================================================================
def fetch_data(source_type):
    if source_type == 'sports':
        print("--- 🌐 抓取台灣運彩 (並行優化) ---")
        def fetch_sports_city(city_code, city_name):
            url = f"{sports_base_url}{city_code}.html"
            local_data = []
            try:
                res = requests.get(url, timeout=10)
                res.encoding = "utf-8"
                soup = BeautifulSoup(res.text, "lxml")
                for tr in soup.find_all("tr"):
                    td = tr.find_all("td")
                    商號, 地址 = "", ""
                    if len(td) >= 2:
                        商號 = td[0].get_text(strip=True)
                        地址 = td[1].get_text(strip=True)
                    if 商號 == "經銷商商號" or not 地址: continue

                    local_data.append({
                        "縣市": city_name, "投注站名稱": 商號,
                        "地址": 地址, "行政區": extract_district(city_name, 地址)
                    })
                return local_data
            except Exception: return []

        sports_data = []
        tasks = list(sports_city_map.items())
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
            future_to_url = {executor.submit(fetch_sports_city, c, n): n for c, n in tasks}
            for future in concurrent.futures.as_completed(future_to_url):
                try: sports_data.extend(future.result())
                except: pass

        df = pd.DataFrame(sports_data)
        if '投注站名稱' not in df.columns and '商號' in df.columns: df.rename(columns={'商號':'投注站名稱'}, inplace=True)
        if not df.empty: df.drop_duplicates(subset=["投注站名稱", "地址"], inplace=True)
        return df

    elif source_type == 'lottery':
        print("\n--- 🌐 抓取台灣彩券 (並行) ---")
        def fetch_lottery_district(county, district):
            lottery_api_url = "https://api.taiwanlottery.com/TLCAPIWeB/Location/AgencyLocation"
            headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            local_data = []
            try:
                params = {"County": county, "City": district, "Addr": ""}
                url = f"{lottery_api_url}?{urlencode(params)}"
                res = requests.get(url, headers=headers, timeout=10)
                result = res.json()
                agency_list = result.get("content", {}).get("locationAgencyLocationList", [])
                if isinstance(agency_list, list):
                    for item in agency_list:
                        address = item.get("strAdd", "").strip()
                        if not address: continue
                        local_data.append({
                            "縣市": county, "行政區": district,
                            "投注站名稱": item.get("strName", "").strip(), "地址": address
                        })
                return local_data
            except Exception: return []

        tasks = [(c, d) for c, ds in county_city_map.items() for d in ds]
        lottery_data = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_url = {executor.submit(fetch_lottery_district, c, d): (c, d) for c, d in tasks}
            for future in concurrent.futures.as_completed(future_to_url):
                try: lottery_data.extend(future.result())
                except: pass

        df = pd.DataFrame(lottery_data, columns=["縣市", "行政區", "投注站名稱", "地址"])
        df.drop_duplicates(subset=["投注站名稱", "地址"], inplace=True)
        return df
    return pd.DataFrame()

# ==============================================================================
# 🟢 主程式入口
# ==============================================================================
if __name__ == "__main__":
    print(f"--- 🔑 正在進行 Google 驗證 ---")
    creds = get_credentials()
    gc = gspread.authorize(creds)
    
    sh = None
    try:
        sh = gc.open(SPREADSHEET_NAME)
        print(f"✅ 成功開啟 Google Sheet: {SPREADSHEET_NAME}")
    except:
        print(f"⚠️ 找不到檔案 {SPREADSHEET_NAME}，正在建立...")
        sh = gc.create(SPREADSHEET_NAME)
        sh.share(SHARING_EMAIL, perm_type='user', role='writer')

    # 1. 抓取資料
    start_time = time.time()
    df_sports = fetch_data('sports')
    df_lottery = fetch_data('lottery')
    end_time = time.time()
    print(f"\n✅ 抓取完成 ({end_time - start_time:.2f} 秒) | 運彩: {len(df_sports)} | 台彩: {len(df_lottery)}")

    if not df_sports.empty:
        df_sports['Google 地圖連結'] = df_sports.apply(lambda r: f'=HYPERLINK("{MAPS_SEARCH_URL}{quote_plus(r["投注站名稱"]+" "+r["地址"])}", "地圖")', axis=1)
    if not df_lottery.empty:
        df_lottery['Google 地圖連結'] = df_lottery.apply(lambda r: f'=HYPERLINK("{MAPS_SEARCH_URL}{quote_plus(r["投注站名稱"]+" "+r["地址"])}", "地圖")', axis=1)

    # 2. 比對資料
    print("\n--- 🔄 資料比對與標記 ---")
    prev_sports, prev_lottery = None, None
    required_cols_list = ["投注站名稱", "縣市", "地址", "原地址", "行政區", "Google 地圖連結"]

    try:
        prev_sports_list = sh.worksheet(PREV_SPORTS_SHEET_NAME).get_all_values()
        if len(prev_sports_list) > 1:
            prev_sports = pd.DataFrame(prev_sports_list[1:], columns=prev_sports_list[0]).fillna('')
    except: pass

    try:
        prev_lottery_list = sh.worksheet(PREV_LOTTERY_SHEET_NAME).get_all_values()
        if len(prev_lottery_list) > 1:
            prev_lottery = pd.DataFrame(prev_lottery_list[1:], columns=prev_lottery_list[0]).fillna('')
    except: pass

    if prev_sports is not None:
        for col in required_cols_list:
            if col not in prev_sports.columns: prev_sports[col] = ''
        df_sports = mark_status(df_sports, prev_sports)
    else:
        df_sports['狀態'], df_sports['原地址'] = '新設', ''

    if prev_lottery is not None:
        for col in required_cols_list:
            if col not in prev_lottery.columns: prev_lottery[col] = ''
        df_lottery = mark_status(df_lottery, prev_lottery)
    else:
        df_lottery['狀態'], df_lottery['原地址'] = '新設', ''

    # 3. 儲存
    print("\n--- ☁️ 儲存資料至 Google Sheets ---")

    # 更新前次資料
    def update_prev_sheet(name, data):
        df_write = data[data['狀態'] != '消失'].copy()
        df_write.drop(columns=["狀態", "原地址"], inplace=True, errors='ignore')
        cols = ["縣市", "行政區", "地址", "投注站名稱", "Google 地圖連結"]
        df_write = df_write[[c for c in cols if c in df_write.columns]].fillna('')

        try: ws = sh.worksheet(name)
        except: ws = sh.add_worksheet(title=name, rows=1, cols=len(cols))
        ws.clear()
        ws.update([df_write.columns.values.tolist()] + df_write.values.tolist(), value_input_option='USER_ENTERED')

    update_prev_sheet(PREV_SPORTS_SHEET_NAME, df_sports)
    update_prev_sheet(PREV_LOTTERY_SHEET_NAME, df_lottery)
    print("✅ 已更新前次比對資料庫")

    # 寫入統計
    try: ws_sum = sh.worksheet("統計")
    except: ws_sum = sh.add_worksheet(title="統計", rows=100, cols=20)

    def get_stats_row(df):
        if df.empty: return [0, 0, 0, 0, 0, 0]
        cnt = Counter(df["狀態"])
        return [len(df), cnt.get("沒變", 0), cnt.get("新設", 0), cnt.get("搬家", 0), cnt.get("消失", 0), cnt.get("恢復營業", 0)]

    stats_sports = get_stats_row(df_sports)
    stats_lottery = get_stats_row(df_lottery)
    new_row_data = [report_time] + stats_sports + stats_lottery

    current_headers = ws_sum.get("A2:M2")
    header_row_2 = ["抓取時間", "總計", "沒變", "新設", "搬家", "消失", "恢復營業", "總計", "沒變", "新設", "搬家", "消失", "恢復營業"]

    if not current_headers or current_headers[0] != header_row_2:
        ws_sum.clear()
        ws_sum.update([["", "台灣運彩", "", "", "", "", "", "台灣彩券", "", "", "", "", ""]], 'A1')
        ws_sum.update([header_row_2], 'A2')
        try:
            ws_sum.merge_cells('B1:G1')
            ws_sum.merge_cells('H1:M1')
        except: pass

    ws_sum.insert_row(new_row_data, index=3, value_input_option='USER_ENTERED')
    print(f"✅ 已更新統計數據")

    # 寫入異動與 Log
    _, changes_s = generate_report(df_sports, "台灣運彩", report_time)
    _, changes_l = generate_report(df_lottery, "台灣彩券", report_time)
    report_changes = pd.concat([changes_s, changes_l], ignore_index=True).fillna('')

    if not report_changes.empty:
        try: ws_ch = sh.worksheet("異動明細")
        except: 
            ws_ch = sh.add_worksheet(title="異動明細", rows=1, cols=len(report_changes.columns))
            ws_ch.update([report_changes.columns.values.tolist()], 'A1')

        ws_ch.insert_rows(report_changes.values.tolist(), row=2, value_input_option='USER_ENTERED')
        update_disappeared_log(sh, report_changes, "綜合", report_time)
        print(f"✅ 已寫入異動明細與 Log")
    else:
        print("ℹ️ 本次無異動資料")