import os
import base64
import requests
import json
import datetime
from google import genai
from google.genai import types

# 1. 核心配置
# 監控目標：微軟官方 SQL Server 更新彙整頁面
SQL_API_URL = "https://api.github.com/repos/MicrosoftDocs/SupportArticles-docs/contents/support/sql/releases/download-and-install-latest-updates.md"
STATE_FILE = "mssql_versions.json"
GEMINI_KEY = os.getenv("GEMINI_API_KEY")

def run_sql_monitor():
    # 取得台灣時間 (UTC+8)
    now = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] === 啟動 MSSQL 專業監控 (強化 SP/CU/GDR 識別) ===")

    try:
        # --- 步驟 A: 獲取微軟最新文件 ---
        resp = requests.get(SQL_API_URL)
        if resp.status_code != 200:
            print(f"[{now}] 錯誤：無法存取 GitHub API (Status: {resp.status_code})")
            return
        
        gh_data = resp.json()
        current_sha = gh_data['sha']

        # --- 步驟 B: 讀取現有歷史紀錄 ---
        db = {}
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE, 'r', encoding='utf-8') as f:
                    db = json.load(f)
                # 確保舊格式相容
                if isinstance(db, list): db = {} 
            except Exception:
                db = {}

        # --- 步驟 C: SHA 安全檢查 (防重複執行) ---
        if db.get("_metadata", {}).get("sha") == current_sha:
            print(f"[{now}] 文件 SHA [{current_sha[:8]}] 未變動，僅更新檢查時間。")
            db["_metadata"]["last_checked"] = now
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(db, f, indent=4, ensure_ascii=False)
            return

        # --- 步驟 D: 準備強效解析 Prompt ---
        print(f"[{now}] 偵測到文件更新，正在解析完整版本鏈...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        # 增加讀取範圍至 25000 字元，確保包含 SQL 2016 舊版資訊
        content_to_parse = md_text[:25000] 

        client = genai.Client(api_key=GEMINI_KEY)
        
        # 強制 Few-shot 範例引導，讓 AI 知道什麼是標籤
        prompt = f"""
        任務：解析 MSSQL 更新日誌。你必須從複雜的表格中提取『型號標籤』與『Build號碼』。
        
        !!! 嚴格提取範例 !!!：
        - 看到 'Cumulative Update 24 (CU24)' -> 標籤填 'CU24'
        - 看到 'Service Pack 3 GDR' -> 標籤填 'SP3-GDR'
        - 看到 'Service Pack 2 CU17' -> 標籤填 'SP2-CU17'
        - 若是 SQL 2025/2022/2019/2017，標籤通常為 'CU x' 或 'GDR'。
        - 若是 SQL 2016，標籤必須包含 SP 資訊 (如 'SP3-GDR')。
        
        解析版本範圍：SQL Server 2025, 2022, 2019, 2017, 2016。

        JSON 欄位：
        - product: 產品名稱 (例: 'SQL Server 2022')
        - full_label: 完整更新標籤 (例: 'CU24', 'SP3-GDR', 'GDR')
        - version: 純 Build 編號 (例: '16.0.4245.2')
        - release_date: 發布日期 (例: 'March 5, 2026')
        - kb_article: KB 編號
        - support_status: 支援狀態 ('Mainstream' 或 'Extended')
        
        待解析內容：
        {content_to_parse}
        """
        
        # 使用 2.0-flash 進行高精度解析
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.0 
            )
        )
        
        new_snapshots = json.loads(response.text)

        # --- 步驟 E: 資料比對與累積存檔 ---
        update_count = 0
        for entry in new_snapshots:
            product_name = entry["product"]
            build_version = entry["version"] # 以 Build 號碼作為唯一鍵

            if product_name not in db:
                db[product_name] = []

            # 比對現有歷史，如果「版本號不存在」則新增
            is_new = True
            for history_item in db[product_name]:
                if history_item.get("version") == build_version:
                    # 如果版本號已存在，但之前沒抓到標籤，則更新它
                    if "full_label" not in history_item or not history_item["full_label"]:
                        history_item.update(entry)
                        print(f" >> 補齊現有版本標籤: {product_name} -> {entry['full_label']}")
                        update_count += 1
                    is_new = False
                    break
            
            if is_new:
                entry["_captured_at"] = now
                db[product_name].insert(0, entry) # 新版本插在最前面
                update_count += 1
                print(f" >> 發現新發布！ {product_name} [{entry['full_label']}] -> {build_version}")

        # --- 步驟 F: 更新 Metadata ---
        db["_metadata"] = {
            "sha": current_sha,
            "last_checked": now,
            "source": "Microsoft Official SQL Release Docs",
            "model_used": "Gemini-2.5-Flash-Lite"
        }

        # --- 步驟 G: 寫入檔案 ---
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
        print(f"[{now}] 執行成功。本次更新/新增 {update_count} 筆資料。")

    except Exception as e:
        print(f"[{now}] 執行異常: {str(e)}")
        raise

if __name__ == "__main__":
    run_sql_monitor()
