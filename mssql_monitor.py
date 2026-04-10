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

def run_sql_monitor():
    # 取得台灣時間 (UTC+8)
    now = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] === 啟動 MSSQL 歷史累積監控 (含 CU 型號識別) ===")

    try:
        # 1. 抓取微軟官方更新文件
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        # 2. 讀取現有紀錄
        db = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                if isinstance(db, list): db = {} 
            except Exception:
                db = {}

        # 3. SHA 比對 (避免重複呼叫 AI)
        if db.get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] 文件 SHA 未變動，僅更新檢查時間。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        # 4. 呼叫 Gemini 進行深度解析
        print(f"[{now}] 開始解析 SQL 2016-2025 更新標籤...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        client = genai.Client(api_key=GEMINI_KEY)
        
        # 修改後的 Prompt：強制拆分型號與版本號
        prompt = f"""
        任務：你是資深 MSSQL DBA，請解析以下 Markdown 並提取各版本的『最新』更新資訊。
        
        要求：
        1. 欄位拆分細化：
           - product: 產品名稱 (如 SQL Server 2022)。
           - update_label: 更新型號標籤 (必須提取如 'CU24', 'CU3', 'GDR', 'SP3' 等內容)。
           - version: 純 Build 編號 (如 16.0.4245.2)。
           - release_date: 發布日期 (如 March 5, 2026)。
           - kb_article: KB 文件編號。
           - support_status: 'Mainstream' 或 'Extended'。
        
        輸出為 JSON 陣列。
        
        文件內容：
        {md_text[:15000]}
        """
        
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.0)
        )
        
        new_snapshots = json.loads(response.text)

        # 5. 累積更新邏輯
        update_count = 0
        for entry in new_snapshots:
            product_name = entry["product"]
            build_version = entry["version"] # 用 Build 號碼當作唯一辨識碼

            if product_name not in db:
                db[product_name] = []

            # 檢查是否已存在
            is_new = True
            for history_item in db[product_name]:
                if history_item.get("version") == build_version:
                    is_new = False
                    break
            
            if is_new:
                entry["_captured_at"] = now
                db[product_name].insert(0, entry) # 新的排在前面
                update_count += 1
                print(f" >> 偵測到新版本！ {product_name} [{entry['update_label']}] -> {build_version}")

        # 6. 更新 Metadata
        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "source": "Microsoft SQL Server Releases",
            "status": "Updated"
        }

        # 7. 存檔
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功。新增 {update_count} 筆，資料庫已更新。")

    except Exception as e:
        print(f"[{now}] 異常: {str(e)}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
