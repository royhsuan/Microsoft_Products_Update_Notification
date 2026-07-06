import os
import base64
import requests
import json
import datetime
import time
from google import genai
from google.genai import types

# 配置
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
GITHUB_URL = "https://api.github.com/repos/MicrosoftDocs/powerbi-docs/contents/powerbi-docs/report-server/changelog.md"
STATE_FILE = "pbirs_version.json"
LOG_FILE = "pbirs_run.log"
HEARTBEAT_FILE = "pbirs_heartbeat.txt"

def write_log(log_file, status, message, max_lines=100):
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{now}] [{status}] {message}\n"
    
    existing_lines = []
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                existing_lines = f.readlines()
        except Exception:
            pass
            
    existing_lines.append(log_line)
    if len(existing_lines) > max_lines:
        existing_lines = existing_lines[-max_lines:]
        
    try:
        with open(log_file, 'w', encoding='utf-8') as f:
            f.writelines(existing_lines)
    except Exception as e:
        print(f"Failed to write log: {e}")

def write_heartbeat(heartbeat_file):
    try:
        utc_now = datetime.datetime.now(datetime.timezone.utc)
        day = utc_now.strftime("%d")
        if day.startswith("0"):
            day = " " + day[1:]
        formatted = utc_now.strftime(f"%a %b {day} %H:%M:%S UTC %Y\n")
        with open(heartbeat_file, 'w', encoding='utf-8') as f:
            f.write(formatted)
    except Exception as e:
        print(f"Failed to write heartbeat: {e}")

def run_monitor():
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")
    
    print(f"[{now}] 開始執行 PBIRS 監控...")
    
    status = "SUCCESS"
    log_msg = "內容未變動，無更新"
    
    try:
        print(f"[{now}] 正在獲取 GitHub 完整更新日誌內容...")
        resp = requests.get(GITHUB_URL)
        if resp.status_code != 200:
            log_msg = f"錯誤：無法存取 GitHub API (HTTP {resp.status_code})"
            print(f"[{now}] {log_msg}")
            status = "ERROR"
            return False
            
        gh_data = resp.json()
        current_sha = gh_data['sha']
        
        # 讀取現有狀態（如果存在）
        history = []
        last_sha = ""
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                history = data if isinstance(data, list) else [data]
                if history: last_sha = history[0].get("sha", "")

        # 比對 SHA
        if last_sha == current_sha:
            log_msg = "內容未變動，且歷史紀錄已存在，結束執行。"
            print(f"[{now}] {log_msg}")
            return False

        print(f"[{now}] 偵測到文件變動或首次執行，開始完整解析歷史資訊...")
        md_text = base64.b64decode(gh_data['content']).decode('utf-8')
        
        client = genai.Client(api_key=GEMINI_KEY)
        
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
        
        max_retries = 5
        new_history = None
        for attempt in range(1, max_retries + 1):
            curr_time = datetime.datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")
            try:
                print(f"[{curr_time}] 呼叫 Gemini API (第 {attempt} 次嘗試)...")
                response = client.models.generate_content(
                    model='gemini-3.1-flash-lite',
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type='application/json',
                        temperature=0.0
                    ),
                )
                new_history = json.loads(response.text)
                break
            except Exception as e:
                curr_time = datetime.datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{curr_time}] Gemini API 呼叫失敗 (第 {attempt} 次): {str(e)}")
                if attempt == max_retries:
                    raise
                sleep_time = 3 * (2 ** (attempt - 1))
                print(f"[{curr_time}] 等待 {sleep_time} 秒後重試...")
                time.sleep(sleep_time)

        if new_history is None:
            raise Exception("未能成功從 Gemini API 取得結果")

        if not isinstance(new_history, list):
            new_history = [new_history]

        if new_history:
            new_history[0]["sha"] = current_sha
            
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_history, f, indent=4, ensure_ascii=False)
        
        log_msg = f"歷史資訊補完成功！共紀錄 {len(new_history)} 個版本。"
        print(f"[{now}] {log_msg}")
        return True

    except Exception as e:
        status = "ERROR"
        log_msg = f"解析失敗: {e}"
        print(f"[{now}] {log_msg}")
        raise
    finally:
        write_log(LOG_FILE, status, log_msg)
        write_heartbeat(HEARTBEAT_FILE)

if __name__ == "__main__":
    run_monitor()
