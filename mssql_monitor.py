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
    print(f"[{now}] === 啟動 MSSQL 全版本歷史累積監控 ===")

    try:
        # 1. 抓取微軟官方更新文件
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API (Status: {resp.status_code})")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        # 2. 讀取現有紀錄 (初始化為字典結構)
        # 結構：{"SQL Server 2022": [歷史列表], "_metadata": {}}
        db = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                # 確保舊資料格式相容，如果是舊的列表格式則清空重新開始
                if isinstance(db, list): db = {} 
            except Exception:
                db = {}

        # 3. 檢查 SHA 避免不必要的 AI 呼叫 (節省額度)
        if db.get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] 官方文件 SHA 未變動，跳過 AI 解析。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        # 4. 呼叫 Gemini 進行 ETL 解析
        print(f"[{now}] 偵測到文件更新，正在請求 AI 解析最新版本資訊...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        client = genai.Client(api_key=GEMINI_KEY)
        
        # 提示詞優化：要求精確的 Build 號碼以利比對
        prompt = f"""
        任務：你是資深 MSSQL DBA。請從以下 Markdown 中提取各版本的最新更新資訊。
        
        要求：
        1. 產品範圍：SQL Server 2025, 2022, 2019, 2017, 2016。
        2. 輸出格式：JSON 陣列。
        3. 欄位：product, latest_cu, latest_gdr, release_date, kb_article, support_status。
        4. 注意：latest_cu 必須包含完整的 Build 編號 (例如 16.0.4245.2)。

        文件內容：
        {md_text[:15000]}
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite', # 建議使用 2.0-flash 兼顧速度與額度
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.0)
        )
        
        new_snapshots = json.loads(response.text)

        # 5. 核心邏輯：比對並補充歷史紀錄
        update_count = 0
        for entry in new_snapshots:
            product_name = entry["product"]
            # 使用 latest_cu 作為版本唯一識別碼 (包含 Build 號)
            version_id = entry["latest_cu"]

            if product_name not in db:
                db[product_name] = []

            # 檢查這個版本是否已經存在於該產品的歷史中
            is_new = True
            for history_item in db[product_name]:
                if history_item.get("latest_cu") == version_id:
                    is_new = False
                    break
            
            if is_new:
                # 將新抓到的版本資訊插入到該產品列表的最前面 (由新到舊)
                entry["_captured_at"] = now # 紀錄我們抓取到的時間
                db[product_name].insert(0, entry)
                update_count += 1
                print(f" >> 發現新發布！ {product_name}: {version_id}")

        # 6. 更新 Metadata
        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "source": "Microsoft Support Articles",
            "status": "Success"
        }

        # 7. 儲存完整的資料庫
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行完畢。本次新增 {update_count} 筆更新，資料庫已存檔。")

    except Exception as e:
        print(f"[{now}] 執行發生異常: {str(e)}")
        # 即使失敗也嘗試更新最後檢查時間，避免 Actions 報錯
        raise

if __name__ == "__main__":
    run_sql_monitor()
