import os
import base64
import requests
import json
import google.generativeai as genai

# 配置
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_URL = "https://api.github.com/repos/MicrosoftDocs/powerbi-docs/contents/powerbi-docs/report-server/changelog.md"
STATE_FILE = "last_version.json"

def run_monitor():
    # 1. 抓取微軟更新文件
    resp = requests.get(GITHUB_URL)
    gh_data = resp.json()
    current_sha = gh_data['sha']
    
    # 2. 讀取上次紀錄的狀態
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            state = json.load(f)
    else:
        state = {"sha": ""}

    # 3. 比對 SHA (聰明檢查：沒變動就收工)
    if state.get("sha") == current_sha:
        print("內容未變動，結束執行。")
        return False

    # 4. 內容有變，交給 Gemini 解析
    print("發現新內容，正在解析...")
    md_text = base64.b64decode(gh_data['content']).decode('utf-8')
    
    genai.configure(api_key=GEMINI_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    prompt = f"請解析此 Power BI 更新日誌並以 JSON 格式回傳最新版本資訊(version, release_date, description): {md_text[:5000]}"
    
    try:
        ai_resp = model.generate_content(prompt, generation_config={"response_mime_type": "application/json"})
        new_data = json.loads(ai_resp.text)
    except Exception as e:
        # 如果失敗，印出 Gemini 回傳的原文字，這能幫我們抓到真正的原因
        print(f"AI 解析階段出錯: {str(e)}")
        if 'ai_resp' in locals():
            print(f"AI 回傳內容: {ai_resp.text}")
        raise  # 讓 GitHub Action 捕捉到錯誤並顯示 Exit Code 1
    
    new_data = json.loads(ai_resp.text)
    
    # 5. 更新狀態檔案
    new_data["sha"] = current_sha
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(new_data, f, indent=4, ensure_ascii=False)
    
    print(f"成功擷取新版本: {new_data['version']}")
    return True

if __name__ == "__main__":
    run_monitor()
