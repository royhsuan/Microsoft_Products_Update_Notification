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
    print(f"[{now}] === 啟動 MSSQL 監控 (確保最新版本優先排列) ===")

    try:
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        db = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                db = json.load(f)
            if isinstance(db, list): db = {} 

        if db.get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] SHA 未變動，更新時間。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        print(f"[{now}] 正在抓取並解析全版本更新...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        client = genai.Client(api_key=GEMINI_KEY)
        
        prompt = f"""
        任務：解析 MSSQL 更新日誌。
        要求：提取 2025, 2022, 2019, 2017, 2016 的所有列出的更新。
        標籤要求：必須包含如 'CU24', 'SP3-GDR' 等標籤。
        
        JSON 欄位：product, full_label, version, release_date, kb_article, support_status
        待解析內容：
        {md_text[:25000]}
        """
        
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
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

            # 去重檢查
            if not any(h.get("version") == build_version for h in db[product_name]):
                entry["_captured_at"] = now
                db[product_name].append(entry) # 先加入列表
                update_count += 1

        # --- 核心改動：強制排序邏輯 ---
        for p_name in db:
            if p_name == "_metadata": continue
            
            # 使用版本號進行降序排列 (Newest First)
            # 將 16.0.4245.2 拆解為數字列表 [16, 0, 4245, 2] 進行精確比較
            db[p_name].sort(
                key=lambda x: [int(d) if d.isdigit() else 0 for d in x.get("version", "0").split('.')],
                reverse=True
            )

        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "source": "Microsoft Docs",
            "sort_order": "Version Descending"
        }

        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功。已根據版本號完成降序排列。")

    except Exception as e:
        print(f"[{now}] 異常: {str(e)}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
