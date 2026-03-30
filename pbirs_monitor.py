import os
import base64
import requests
import json
from google import genai
from google.genai import types

# 配置
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_URL = "https://api.github.com/repos/MicrosoftDocs/powerbi-docs/contents/powerbi-docs/report-server/changelog.md"
STATE_FILE = "last_version.json"

def run_monitor():
    print("正在獲取 GitHub 完整更新日誌內容...")
    resp = requests.get(GITHUB_URL)
    gh_data = resp.json()
    current_sha = gh_data['sha']
    
    # 讀取現有狀態（如果存在）
    history = []
    last_sha = ""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 兼容舊格式：如果是列表就直接讀取，如果是字典就包成列表
            history = data if isinstance(data, list) else [data]
            # 嘗試從第一筆資料拿到上次紀錄的 SHA
            if history: last_sha = history[0].get("sha", "")

    # 比對 SHA（如果是第一次跑，或者是文件有變動才繼續）
    if last_sha == current_sha:
        print("內容未變動，且歷史紀錄已存在，結束執行。")
        return False

    print("偵測到文件變動或首次執行，開始完整解析歷史資訊...")
    md_text = base64.b64decode(gh_data['content']).decode('utf-8')
    
    client = genai.Client(api_key=GEMINI_KEY)
    
    # 【關鍵：調整指令】要求 AI 擷取所有版本
    prompt = f"""
    任務：將 Power BI Report Server 的更新日誌轉換為完整的 JSON 歷史紀錄。
    
    要求：
    1. 掃描整份文件，提取「所有」列出的版本。
    2. 輸出為 JSON 陣列 (Array)，每個物件包含：
       - version: 版本號與 Build 號。
       - release_date: 發布日期。
       - report_server_updates: 伺服器更新要點 (繁中列表)。
       - desktop_updates: Desktop RS 版更新要點 (繁中列表)。
       - download_url: 下載連結。
    3. 按照發布日期「由新到舊」排序。

    文件內容：
    {md_text[:12000]} 
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.0-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0.0
            ),
        )
        new_history = json.loads(response.text)
        
        # 確保 new_history 是一個列表
        if not isinstance(new_history, list):
            new_history = [new_history]

        # 在最新的一筆資料中存入當前的 SHA，方便下次比對
        if new_history:
            new_history[0]["sha"] = current_sha
        
    except Exception as e:
        print(f"解析失敗: {e}")
        raise 

    # 5. 儲存完整的歷史陣列
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(new_history, f, indent=4, ensure_ascii=False)
    
    print(f"歷史資訊補完成功！共紀錄 {len(new_history)} 個版本。")
    return True

if __name__ == "__main__":
    run_monitor()
