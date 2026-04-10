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
    將微軟常用的日期格式轉換為可排序的 datetime 物件
    處理格式如: "March 12, 2026" 或 "March 2026"
    """
    formats = ["%B %d, %Y", "%B %Y"]
    for fmt in formats:
        try:
            return datetime.datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    # 如果都解析失敗，回傳一個極小的日期確保排在最後
    return datetime.datetime(1900, 1, 1)

def run_sql_monitor():
    now_ts = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    now = now_ts.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] === 啟動 MSSQL 監控 (發布日期排序模式) ===")

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
            print(f"[{now}] SHA 未變動，跳過 AI 解析。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        # 4. 呼叫 Gemini 解析
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        client = genai.Client(api_key=GEMINI_KEY)
        
        prompt = f"""
        任務：解析 MSSQL 更新日誌。
        要求：提取 2025-2016 所有版本。
        標籤要求：必須包含如 'CU24', 'SP3-GDR' 等明確標籤。
        
        JSON 欄位：product, full_label, version, release_date, kb_article, support_status
        發布日期格式請統一如：'March 12, 2026' 或 'March 2026'。
        
        待解析內容：
        {md_text[:25000]}
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.0)
        )
        
        new_snapshots = json.loads(response.text)

        # 5. 更新資料庫
        update_count = 0
        for entry in new_snapshots:
            p_name = entry["product"]
            v_code = entry["version"]

            if p_name not in db:
                db[p_name] = []

            # 去重：如果 Build 號碼沒出現過，才加入
            if not any(h.get("version") == v_code for h in db[p_name]):
                entry["_captured_at"] = now
                db[p_name].append(entry)
                update_count += 1

        # --- 核心改動：根據發布日期進行排序 ---
        for p_name in db:
            if p_name == "_metadata": continue
            
            # 排序邏輯：
            # 1. 主要依據 release_date (由新到舊)
            # 2. 次要依據 version (如果日期相同，版本號高的在前)
            db[p_name].sort(
                key=lambda x: (
                    parse_ms_date(x.get("release_date", "January 1900")),
                    [int(d) if d.isdigit() else 0 for d in x.get("version", "0").split('.')]
                ),
                reverse=True
            )

        # 6. Metadata 與存檔
        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "sort_order": "Release Date Descending"
        }

        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功。已完成日期權重排序。")

    except Exception as e:
        print(f"[{now}] 異常: {str(e)}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
