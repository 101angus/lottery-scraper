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
# 🔑 關鍵修改：從環境變數讀取金鑰 (GitHub Actions 專用)
# ==============================================================================
def get_credentials():
    # 在 GitHub Secrets 中設定 GCP_SA_KEY
    json_str = os.environ.get('GCP_SA_KEY')
    if not json_str:
        # 如果本機測試沒有設定環境變數，這裡會報錯提醒
        raise ValueError("⚠️ 找不到環境變數 GCP_SA_KEY，請檢查 GitHub Secrets 設定")
    
    key_dict = json.loads(json_str)
    creds = service_account.Credentials.from_service_account_info(
        key_dict, 
        scopes=['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    )
    return creds

# ==============================================================================
# 🛠️ 核心函式：API 解析 (多表格 + 4欄位 + 統計資訊)
# ==============================================================================
async def fetch_prize_via_api_async(session, news_url, target_game_id):
    """
    透過 API 取得新聞內容，解析出該刮刮樂的所有獎金結構與統計資訊。
    """
    if not news_url: return ""
    
    try:
        clean_url = news_url.split('#')[0]
        news_id = clean_url.rstrip('/').split('/')[-1]
        api_url = f"{API_BASE_URL}{news_id}"
        
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.taiwanlottery.com/"}

        async with session.get(api_url, headers=headers, timeout=10) as resp:
            if resp.status != 200: return ""
            data = await resp.json()
            
            raw_html = ""
            if isinstance(data, dict) and "content" in data:
                if isinstance(data["content"], str): raw_html = data["content"]
                elif isinstance(data["content"], dict): raw_html = data["content"].get("content", "")
            
            if not raw_html: return ""

            # 預處理
            raw_html = raw_html.replace("<br>", "||BR||").replace("<br/>", "||BR||")
            soup = BeautifulSoup(raw_html, 'html.parser')
            final_parts = []

            # 1. 定位起點 (ID 錨點)
            anchor = soup.find("a", attrs={"id": str(target_game_id)}) or soup.find("a", attrs={"name": str(target_game_id)})
            
            # 針對新版網頁，若無傳統錨點，尋找包含「期數」與目標 ID 的標題
            if not anchor:
                for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'div', 'p', 'span']):
                    txt = tag.get_text(strip=True)
                    # 避免抓到外層容器，限制字數長度
                    if "期數" in txt and str(target_game_id) in txt and len(txt) < 50:
                        anchor = tag
                        break

            target_tables = []
            if anchor:
                # 從錨點開始，往後找所有表格，直到遇到下一個錨點或下一個遊戲
                curr = anchor
                while True:
                    curr = curr.find_next()
                    if not curr: break
                    
                    if curr.name == "table":
                        target_tables.append(curr)
                    
                    # 停止條件：遇到下一個 ID 錨點或另一個遊戲的標題
                    stop_parsing = False
                    if curr.name == "a" and (curr.get("id") or curr.get("name")):
                        aid = str(curr.get("id") or curr.get("name")).strip()
                        if aid != str(target_game_id) and re.match(r'^\d{3,4}$', aid):
                            stop_parsing = True
                    elif curr.name in ["h1", "h2", "h3", "h4"]:
                        txt = curr.get_text(strip=True)
                        if "期數" in txt and len(txt) < 50:
                            m = re.search(r'\d{3,4}', txt)
                            if m and m.group() != str(target_game_id):
                                stop_parsing = True
                    
                    if stop_parsing:
                        break
            else:
                # 備案：單一遊戲頁面，抓所有表格
                target_tables = soup.find_all("table")

            if not target_tables: return ""

            # 2. 解析所有表格
            for table in target_tables:
                rows = table.find_all("tr")
                for row in rows:
                    cols = row.find_all(["td", "th"])
                    
                    if len(cols) >= 2:
                        # --- 邏輯 A: 一般獎金表 (非 summy) ---
                        if "summy" not in str(table.get("class", [])):
                            # 準備配對組 (支援 4 欄位)
                            pairs = []
                            if len(cols) >= 4:
                                pairs.append((cols[0], cols[1]))
                                pairs.append((cols[2], cols[3]))
                            elif len(cols) >= 2:
                                pairs.append((cols[0], cols[1]))

                            for col_prize, col_count in pairs:
                                # 提取清單內容 (支援 ul/li)
                                def extract_items(cell):
                                    ul = cell.find("ul")
                                    if ul: return [li.get_text(strip=True) for li in ul.find_all("li")]
                                    txt = cell.get_text("|", strip=True)
                                    return [t.strip() for t in txt.split("|") if t.strip()]

                                prizes = extract_items(col_prize)
                                counts = extract_items(col_count)

                                for p, c in zip(prizes, counts):
                                    p_clean = p.replace(":", "").strip()
                                    c_clean = c.replace(",", "").strip()

                                    if not p_clean or not c_clean: continue
                                    if "獎項" in p_clean or "金額" in p_clean or "張數" in c_clean or "中獎" in c_clean: continue
                                    
                                    # 驗證獎項格式
                                    is_valid = ("NT" in p_clean or "$" in p_clean or "元" in p_clean) or \
                                               re.search(r'[頭壹貳參肆伍陸柒捌玖\d]+獎', p_clean)
                                    if is_valid:
                                        final_parts.append(f"{p_clean}|{c_clean}")

                        # --- 邏輯 B: 統計表 (summy) ---
                        else:
                            key = cols[0].get_text(strip=True)
                            val = cols[1].get_text(strip=True).replace(",", "")
                            
                            if "發行" in key or "中獎" in key:
                                final_parts.append(f"{key}|{val}")

            return "|".join(final_parts)

    except Exception:
        return ""

# ==============================================================================
# 🚀 主流程：Playwright 列表抓取
# ==============================================================================
async def scrape_all_scratchcards():
    print("🚀 啟動爬蟲程式...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        print("🌐 連線至台灣彩券官網...")
        try:
            await page.goto(TAIWAN_LOTTERY_URL, timeout=60000)
            await page.wait_for_selector('.list .card', timeout=30000)
        except:
            print("⚠️ 網站載入逾時或失敗。")
            await browser.close()
            return pd.DataFrame()

        data_list = []
        seen_ids = set()
        
        async with aiohttp.ClientSession() as session:
            page_num = 1
            while True:
                print(f"📄 正在處理第 {page_num} 頁...")
                cards = await page.locator('.list .card').all()
                print(f"    - 發現 {len(cards)} 款刮刮樂...")
                
                new_data_on_page = False

                for i, card in enumerate(cards):
                    try:
                        # 1. 取得 ID
                        lottery_id = ""
                        spans = await card.locator('span').all()
                        for s in spans:
                            txt = await s.text_content()
                            if txt and txt.strip().isdigit() and len(txt.strip()) >= 3:
                                lottery_id = txt.strip()
                                break
                        
                        if not lottery_id or lottery_id in seen_ids: continue
                        seen_ids.add(lottery_id)
                        new_data_on_page = True

                        # 2. 取得名稱
                        game_name = "未命名"
                        h2 = await card.locator('h2').count()
                        if h2 > 0: game_name = await card.locator('h2').first.text_content()

                        # 3. 取得詳細資訊 (XPath)
                        details = {}
                        detail_keys = ["售價", "發行日", "下市日", "兌獎截止日", "發行張數", "銷售率", "頭獎張數", "最高獎金張數", "頭獎未兌領張數", "最高獎金未兌領張數", "最高獎金"]
                        
                        full_text = await card.inner_text()
                        text_lines = full_text.split('\n')
                        for line in text_lines:
                            line = line.strip()
                            for key in detail_keys:
                                if key in line:
                                    val = line.replace(key, "").strip()
                                    if not val: 
                                        idx = text_lines.index(line)
                                        if idx + 1 < len(text_lines): val = text_lines[idx+1].strip()
                                    if key not in details or len(val) > len(details.get(key, "")):
                                        details[key] = val

                        # 4. 取得連結與獎金結構
                        link = ""
                        links = await card.locator('a').all()
                        for l in links:
                            href = await l.get_attribute('href')
                            if href and ("news" in href or "prize" in href):
                                link = href
                                break
                        
                        structure_str = ""
                        if link:
                            # 傳入 ID 進行定位
                            structure_str = await fetch_prize_via_api_async(session, link, lottery_id)

                        # 5. 彙整單筆資料
                        row = {
                            "編號": lottery_id,
                            "名稱": game_name,
                            "售價": details.get("售價", "-").replace("$", ""),
                            "最高獎金": details.get("最高獎金", "-"),
                            "發行日": details.get("發行日", "-"),
                            "下市日": details.get("下市日", "-"),
                            "兌獎截止日": details.get("兌獎截止日", "-"),
                            "銷售率": details.get("銷售率", "-"),
                            "頭獎張數": details.get("頭獎張數", details.get("最高獎金張數", "-")),
                            "頭獎未兌領": details.get("頭獎未兌領張數", details.get("最高獎金未兌領張數", "-")),
                            "獎金結構連結": link
                        }

                        # 動態展開
                        if structure_str:
                            parts = structure_str.split('|')
                            for k in range(0, len(parts)-1, 2):
                                p_key = parts[k].strip()
                                p_val = parts[k+1].strip()
                                if p_key and p_val:
                                    row[p_key] = p_val

                        data_list.append(row)

                    except Exception:
                        continue
                
                # 翻頁判斷
                if not new_data_on_page and page_num > 1:
                    print("🔚 已無新資料，停止翻頁。")
                    break

                next_btn = page.locator("button.btn-next")
                if await next_btn.count() > 0 and not await next_btn.is_disabled():
                    await next_btn.click()
                    await page.wait_for_load_state('networkidle')
                    await asyncio.sleep(2)
                    page_num += 1
                else:
                    print("🔚 已達最後一頁。")
                    break

        await browser.close()
        return pd.DataFrame(data_list)

# ==============================================================================
# 💰 輔助函式：獎金排序
# ==============================================================================
def money_sorter(money_str):
    try:
        clean = re.sub(r'[^\d]', '', str(money_str))
        return int(clean) if clean else -1
    except:
        return -1

# ==============================================================================
# 💾 資料儲存：寫入 Google Sheets
# ==============================================================================
def save_to_google_sheet(df):
    if df.empty:
        print("⚠️ 無資料可寫入。")
        return

    try:
        print("💾 正在寫入 Google Sheet...")
        df = df.fillna("")
        
        # 使用新的驗證函式取得 Creds
        creds = get_credentials()
        gc = gspread.authorize(creds)
        
        try: sh = gc.open(SPREADSHEET_NAME)
        except: 
            # 如果找不到，嘗試建立 (注意：這通常需要分享權限，建議先手動建好試算表)
            print(f"⚠️ 找不到試算表 '{SPREADSHEET_NAME}'，嘗試建立...")
            sh = gc.create(SPREADSHEET_NAME)
            sh.share("101angus@gmail.com", perm_type='user', role='writer')
        
        try: ws = sh.worksheet(SHEET_TAB_NAME); ws.clear()
        except: ws = sh.add_worksheet(SHEET_TAB_NAME, 1, 1)
            
        fixed_cols = ["編號", "名稱", "售價", "最高獎金", "發行日", "下市日", "兌獎截止日", "銷售率", "頭獎張數", "頭獎未兌領"]
        link_col = ["獎金結構連結"]
        
        all_cols = df.columns.tolist()
        dynamic_cols = [c for c in all_cols if c not in fixed_cols and c not in link_col]
        
        stats_cols = []
        money_cols = []
        
        for c in dynamic_cols:
            if "發行" in c or "中獎" in c:
                stats_cols.append(c)
            else:
                money_cols.append(c)
        
        # 排序：統計(發行優先) -> 獎金(大到小)
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
        print(f"   - 包含 {len(money_cols)} 個獎金欄位")
        print(f"📎 連結: {sh.url}")
        
    except Exception as e:
        print(f"⚠️ 存檔失敗: {e}")

# ==============================================================================
# 🟢 程式進入點
# ==============================================================================
if __name__ == "__main__":
    # 使用新的事件迴圈啟動方式
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    df_result = loop.run_until_complete(scrape_all_scratchcards())
    save_to_google_sheet(df_result)
