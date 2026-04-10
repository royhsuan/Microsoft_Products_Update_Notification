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
    print(f"[{now}] === 啟動 MSSQL 全版本深度監控 ===")

    try:
        # 1. 抓取微軟官方更新文件
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API (Status: {resp.status_code})")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        # 2. 讀取現有紀錄
        history = []
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                history = json.load(f)

        # 3. SHA 比對
        if history and history[0].get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] 文件無變動。更新檢查時間...")
            history[0]["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=4, ensure_ascii=False)
            return

        # 4. 呼叫 Gemini 進行 ETL 解析
        print(f"[{now}] 偵測到文件更新或首次執行，開始解析 SQL 2025-2016 歷史資訊...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        client = genai.Client(api_key=GEMINI_KEY)
        
        prompt = f"""
        任務：你是資深 MSSQL DBA。請解析以下更新日誌並提取『所有』SQL Server 版本的狀態。
        
        解析要求：
        1. 涵蓋版本：SQL Server 2025, 2022, 2019, 2017, 2016。
        2. 欄位定義：
           - product: 產品名稱 (如 SQL Server 2022)。
           - latest_cu: 最新累積更新名稱與 Build 號。
           - latest_gdr: 最新安全性更新名稱與 Build 號 (若無則填 N/A)。
           - release_date: 最近一次更新的日期。
           - kb_article: 相關的 KB 文件編號。
           - support_status: 根據內容判斷是否為 'Mainstream', 'Extended', 或 'End of Support'。
        
        請輸出為 JSON 陣列。內容：
        {md_text[:15000]}
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.0)
        )
        
        new_data = json.loads(response.text)

        # 5. 加上 Metadata 並存檔
        metadata = {
            "_metadata": {
                "sha": current_sha,
                "last_checked": now,
                "source": "Microsoft Support Articles"
            }
        }
        # 組合資料：Metadata 放在最前面，方便 Actions 比對
        final_output = [metadata] + new_data

        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功！已擷取 {len(new_data)} 個版本的更新資訊。")

    except Exception as e:
        print(f"[{now}] 執行過程發生異常: {e}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
