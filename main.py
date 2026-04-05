import os
import json
import logging
import requests
import cloudscraper
import time
from bs4 import BeautifulSoup
from datetime import datetime

# --- KONFIGURASI LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- BACA SECRETS (DARI GITHUB ACTIONS) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

DB_FILE = "processed_jobs.json"

# --- TARGET SCRAPING ---
# 1. LinkedIn (via Guest API yang diizinkan) dengan filter rentang rilis <= 3 Minggu (r1814400) dan Lokasi Jakarta
LINKEDIN_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=human%20resources&location=Greater%20Jakarta&f_TPR=r1814400&start=0"
# 2. JobStreet (URL Search filter lokasi Jakarta, filter minimum gaji 7.5 Juta dan sort by date terbaru)
JOBSTREET_URL = "https://www.jobstreet.co.id/id/job-search/hr-jobs-in-jakarta/?salary=7500000&salary-type=monthly&sortmode=ListedDate"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

# --- MIDDLE-HANYA, LEBIH LUAS ---
KEYWORDS = [
    "hr", "human resource", "human capital", "recruitment", "talent",
    "personalia", "personnel", "hcbp", "hrbp"
]

# --- FILTER LOKASI JABODETABEK ---
LOCATION_KEYWORDS = ["jakarta", "bogor", "depok", "tangerang", "bekasi", "jabodetabek", "banten", "remote", "wfh", "indonesia"]

def load_processed_jobs():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Gagal membaca history ID loker: {e}")
    return []

def save_processed_jobs(jobs_list):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(jobs_list, f, indent=4)
    except Exception as e:
        logging.error(f"Gagal menyimpan ke history ID loker: {e}")

def send_telegram_message(job):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram belum di-set. Melewati pesan test.")
        return False

    text = f"🎯 *Loker HR Potensial Ditemukan!* 🎯\n\n"
    text += f"💼 *Posisi:* {job['title']}\n"
    text += f"🏢 *Perusahaan:* {job.get('company', 'Tidak disebut')}\n"
    text += f"📍 *Lokasi:* {job.get('location', 'Menyesuaikan')}\n"
    
    if job.get('salary'):
        text += f"💰 *Gaji Estimasi:* {job['salary']}\n"
        
    text += f"⏳ *Diposting:* {job.get('time_ago', 'Cek Lamaran')}\n"
    text += f"✨ *Sumber:* {job['platform']}\n\n"
    text += f"🔗 *Link Lamaran:*\n{job['link']}"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Memisahkan CHAT_ID jika lebih dari satu (menggunakan pemisah koma)
    chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()]
    success_any = False
    
    for chat_id in chat_ids:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": False}
        try:
            response = requests.post(url, json=payload)
            response.raise_for_status()
            logging.info(f"✅ Notif sukses ke Telegram ID {chat_id}: {job['title']}")
            success_any = True
        except requests.exceptions.RequestException as e:
            logging.error(f"❌ Gagal mengirim notif ke Telegram ID {chat_id}: {e}")
            
    return success_any

def scrape_linkedin():
    logging.info("🕷️ Memulai scraping LinkedIn Jobs API...")
    found = []
    try:
        response = requests.get(LINKEDIN_URL, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(response.text, 'html.parser')
        job_cards = soup.find_all("div", class_="base-search-card")[:15]
        
        for card in job_cards:
            title_tag = card.find("h3", class_="base-search-card__title")
            title = title_tag.text.strip() if title_tag else ""
            if not title: continue
            
            link = card.find("a", class_="base-card__full-link")['href']
            jid = link.split('?')[0].split('-')[-1]
            
            company = card.find("h4", class_="base-search-card__subtitle").text.strip() if card.find("h4", class_="base-search-card__subtitle") else ""
            location = card.find("span", class_="job-search-card__location").text.strip() if card.find("span", class_="job-search-card__location") else ""
            
            # Filter Lokasi Python-side
            if not any(lk in location.lower() for lk in LOCATION_KEYWORDS):
                continue
            
            time_tag = card.find("time")
            time_raw = time_tag.text.strip() if time_tag else "Baru Saja"
            if time_tag and time_tag.get('datetime'):
                try:
                    if (datetime.now() - datetime.strptime(time_tag.get('datetime'), "%Y-%m-%d")).days > 21:
                        continue 
                except Exception:
                    pass
            
            if any(kw in title.lower() for kw in KEYWORDS):
                found.append({
                    "id": f"li-{jid}", "title": title, "company": company, "location": location,
                    "time_ago": time_raw.capitalize(), "link": link, "platform": "LinkedIn"
                })
    except Exception as e:
        logging.error(f"LinkedIn scrape error: {e}")
    return found

def scrape_jobstreet():
    logging.info("🕷️ Memulai scraping Jobstreet (Dengan filter bawaan Min: 7.5 Jt)...")
    found = []
    # Menggunakan cloudscraper agar tidak dihadang secara instan oleh Cloudflare WAF JobStreet
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
    try:
        response = scraper.get(JOBSTREET_URL, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        cards = soup.find_all("article", {"data-automation": "normalJob"})[:15]
        for card in cards:
            title_tag = card.find("a", {"data-automation": "jobTitle"})
            if not title_tag: continue
            
            title = title_tag.text.strip()
            link = "https://www.jobstreet.co.id" + title_tag['href']
            jid = link.split('?')[0].split('/')[-1]
            
            company_tag = card.find("a", {"data-automation": "jobCompany"})
            company = company_tag.text.strip() if company_tag else ""
            
            location_tag = card.find("span", {"data-automation": "jobLocation"})
            location = location_tag.text.strip() if location_tag else ""
            
            # Filter Lokasi Python-side
            if not any(lk in location.lower() for lk in LOCATION_KEYWORDS):
                continue
            
            salary_tag = card.find("span", {"data-automation": "jobSalary"})
            salary = salary_tag.text.strip() if salary_tag else "Sesuai Filter Jobstreet (Min. 7.5 Jt)"
            
            if any(kw in title.lower() for kw in KEYWORDS):
                found.append({
                    "id": f"js-{jid}", "title": title, "company": company, "location": location,
                    "salary": salary, "time_ago": "Baru berdasarkan urutan web", "link": link, "platform": "JobStreet"
                })
    except Exception as e:
        logging.error(f"Jobstreet scrape error: {e}")
    return found

def main():
    processed = load_processed_jobs()
    
    # Menggabungkan hasil scrape kedua situs tersebut
    jobs = scrape_linkedin() + scrape_jobstreet()
    
    new_jobs_count = 0
    for job in jobs:
        if job["id"] not in processed:
            if send_telegram_message(job):
                processed.append(job["id"])
                new_jobs_count += 1
                time.sleep(2) # Delay perlindungan API Telegram spam
    
    if new_jobs_count > 0:
        save_processed_jobs(processed)
        logging.info(f"Selesai berjalan. Berhasil mengirimkan {new_jobs_count} loker segar (Middle ke atas).")
    else:
        logging.info("Selesai. Tidak ada yang loker masuk spesifikasi baru yang belum dikirimkan.")

if __name__ == "__main__":
    main()
