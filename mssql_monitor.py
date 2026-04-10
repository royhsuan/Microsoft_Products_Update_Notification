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
    now = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] === 啟動 MSSQL 專業監控 (含 SP/CU/GDR 完整標籤) ===")

    try:
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        db = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                if isinstance(db, list): db = {} 
            except Exception:
                db = {}

        if db.get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] 文件 SHA 未變動，更新檢查時間。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        print(f"[{now}] 偵測到更新，解析完整版本鏈...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        client = genai.Client(api_key=GEMINI_KEY)
        
        # 強化 Prompt：特別針對 SQL 2016 的 SP 架構進行訓練
        prompt = f"""
        任務：你是資深 MSSQL DBA，請解析 Markdown 並提取各版本的『最新』更新資訊。
        
        要求：
        1. 針對 SQL Server 2016：
           - 必須識別 Service Pack (SP) 層級。
           - 標籤應完整，例如 'SP3-GDR' 或 'SP2-CU17'。
        2. 針對 SQL Server 2017-2025：
           - 識別 CU 或 GDR 標籤。
        3. 欄位定義：
           - product: 產品名稱。
           - full_label: 完整更新名稱 (例: '2016 SP3 GDR', '2022 CU24')。
           - version: 純 Build 編號 (例: 13.0.6480.4)。
           - release_date: 發布日期。
           - kb_article: KB 編號。
           - support_status: 支援狀態。
        
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

        update_count = 0
        for entry in new_snapshots:
            product_name = entry["product"]
            build_version = entry["version"]

            if product_name not in db:
                db[product_name] = []

            # 比對 Build 號碼
            if not any(h.get("version") == build_version for h in db[product_name]):
                entry["_captured_at"] = now
                db[product_name].insert(0, entry)
                update_count += 1
                print(f" >> 偵測到新版本！ {product_name} [{entry['full_label']}] -> {build_version}")

        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "source": "Microsoft SQL Server Releases",
            "info": "Captured with SP/CU/GDR context"
        }

        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功。新增 {update_count} 筆。")

    except Exception as e:
        print(f"[{now}] 異常: {str(e)}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
