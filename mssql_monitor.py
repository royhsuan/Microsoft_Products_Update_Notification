import os
import base64
import requests
import json
import datetime
from google import genai
from google.genai import types

# 核心配置
SQL_API_URL = "https://api.github.com/repos/MicrosoftDocs/SupportArticles-docs/contents/support/sql/releases/download-and-install-latest-updates.md"
STATE_FILE = "mssql_versions.json"
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def parse_ms_date(date_str):
    """
    將微軟日期字串轉換為可排序物件，處理 None 或空值。
    """
    if not date_str or not isinstance(date_str, str):
        return datetime.datetime(1900, 1, 1)
        
    formats = ["%B %d, %Y", "%B %Y"]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return datetime.datetime(1900, 1, 1)

def run_sql_monitor():
    # 修正 DeprecationWarning: 使用 timezone-aware UTC
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now_ts = datetime.datetime.now(tz_tw)
    now = now_ts.strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[{now}] === 啟動 MSSQL 監控 (日期排序 + 容錯強化版) ===")

    try:
        # 1. 抓取微軟官方文件
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        # 2. 讀取現有紀錄
        db = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
            if isinstance(db, list): db = {} 

        # 3. SHA 比對
        if db.get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] SHA 未變動，僅更新檢查時間。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        # 4. 呼叫 Gemini 解析
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        client = genai.Client(api_key=GEMINI_KEY)
        
        prompt = f"""
        任務：解析 MSSQL 更新日誌。
        要求：提取所有 SQL 版本 (2016-2025) 的最新與歷史更新。
        標籤：必須包含 'CU24', 'SP3-GDR' 等。
        JSON 欄位：product, full_label, version, release_date, kb_article, support_status
        
        待解析內容：
        {md_text[:25000]}
        """
        
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.0)
        )
        
        new_snapshots = json.loads(response.text)

        # 5. 更新資料庫
        update_count = 0
        for entry in new_snapshots:
            p_name = entry.get("product")
            v_code = entry.get("version")
            
            if not p_name or not v_code: continue

            if p_name not in db:
                db[p_name] = []

            # 去重：檢查該版本 Build 號是否已存在
            if not any(h.get("version") == v_code for h in db[p_name]):
                entry["_captured_at"] = now
                db[p_name].append(entry)
                update_count += 1

        # 6. 強制排序邏輯 (修復 AttributeError)
        for p_name in list(db.keys()):
            if p_name == "_metadata": continue
            
            db[p_name].sort(
                key=lambda x: (
                    parse_ms_date(x.get("release_date")),
                    # 關鍵修復：(x.get("version") or "0") 確保不會對 None 進行 split
                    [int(d) if d.isdigit() else 0 for d in (x.get("version") or "0").split('.')]
                ),
                reverse=True
            )

        # 7. Metadata 與存檔
        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "sort_logic": "Date desc, Version desc"
        }

        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功。已完成日期排序，本次新增 {update_count} 筆。")

    except Exception as e:
        print(f"[{now}] 執行中斷，錯誤原因: {str(e)}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
